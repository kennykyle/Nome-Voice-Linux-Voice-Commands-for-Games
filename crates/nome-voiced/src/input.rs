use std::{
    collections::{HashMap, HashSet},
    error::Error,
    fmt,
    fs::{File, OpenOptions},
    io::{self, Write},
    os::{
        unix::fs::OpenOptionsExt,
        unix::io::AsRawFd,
    },
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex, MutexGuard,
    },
    thread,
    time::{Duration, Instant},
};

use nome_voice_core::{Action, MouseButton};

const UINPUT_PATH: &str = "/dev/uinput";
const UINPUT_MAX_NAME_SIZE: usize = 80;
const ABS_CNT: usize = 64;
/// How often a waiting action checks whether it was cancelled.
const CANCEL_POLL: Duration = Duration::from_millis(5);

const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;
const UI_SET_EVBIT: libc::c_ulong = 0x4004_5564;
const UI_SET_KEYBIT: libc::c_ulong = 0x4004_5565;

const BUS_USB: u16 = 0x03;
const EV_SYN: u16 = 0x00;
const EV_KEY: u16 = 0x01;
const SYN_REPORT: u16 = 0;

#[derive(Debug)]
pub struct InputError(String);

impl fmt::Display for InputError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl Error for InputError {}

impl From<io::Error> for InputError {
    fn from(error: io::Error) -> Self {
        Self(error.to_string())
    }
}

type InputResult<T> = Result<T, InputError>;

/// Where key events go: the real uinput device, or nowhere for a dry run.
pub trait KeySink: Send {
    fn key(&mut self, code: u16, value: i32) -> InputResult<()>;
}

/// Swallows events. Used by NOME_VOICED_DRY_RUN=1 so tests never touch the desktop.
#[derive(Debug, Default)]
pub struct NullSink;

impl KeySink for NullSink {
    fn key(&mut self, _code: u16, _value: i32) -> InputResult<()> {
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Completed,
    Cancelled,
}

struct KeyState {
    sink: Box<dyn KeySink>,
    /// Every key this daemon currently holds down, whatever action pressed it.
    down: HashSet<u16>,
    toggled: HashSet<u16>,
}

/// Runs action sequences one at a time; release_all() can interrupt them at any point.
///
/// Sequences never hold the key lock while waiting, so release_all() always gets in
/// within a few milliseconds. Every key press checks the cancel generation under the
/// key lock, so no key can go down after an emergency stop has released everything.
pub struct ActionExecutor {
    keys: Mutex<KeyState>,
    sequence: Mutex<()>,
    generation: AtomicU64,
}

impl fmt::Debug for ActionExecutor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ActionExecutor")
            .field("generation", &self.generation.load(Ordering::SeqCst))
            .finish_non_exhaustive()
    }
}

impl ActionExecutor {
    pub fn new() -> InputResult<Self> {
        let device = UInputDevice::new("Nome Voice Virtual Input", &virtual_key_codes())?;
        Ok(Self::with_sink(Box::new(device)))
    }

    pub fn dry_run() -> Self {
        Self::with_sink(Box::new(NullSink))
    }

    pub fn with_sink(sink: Box<dyn KeySink>) -> Self {
        Self {
            keys: Mutex::new(KeyState {
                sink,
                down: HashSet::new(),
                toggled: HashSet::new(),
            }),
            sequence: Mutex::new(()),
            generation: AtomicU64::new(0),
        }
    }

    /// Run actions in order. Returns Cancelled if release_all() ran meanwhile, including
    /// while this sequence was still waiting for an earlier one to finish.
    pub fn execute(&self, actions: &[Action]) -> InputResult<Outcome> {
        let generation = self.generation.load(Ordering::SeqCst);
        let _turn = self.sequence.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        for action in actions {
            if self.cancelled(generation) {
                return Ok(Outcome::Cancelled);
            }
            if self.execute_action(action, generation)? == Outcome::Cancelled {
                return Ok(Outcome::Cancelled);
            }
        }
        Ok(Outcome::Completed)
    }

    /// Cancel running and queued sequences and release every key this daemon holds.
    pub fn release_all(&self) -> InputResult<()> {
        self.generation.fetch_add(1, Ordering::SeqCst);
        let mut keys = self.lock_keys();
        let down = keys.down.drain().collect::<Vec<_>>();
        keys.toggled.clear();
        for code in down {
            keys.sink.key(code, 0)?;
        }
        Ok(())
    }

    pub fn held_key_count(&self) -> usize {
        self.lock_keys().down.len()
    }

    fn lock_keys(&self) -> MutexGuard<'_, KeyState> {
        self.keys.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn cancelled(&self, generation: u64) -> bool {
        self.generation.load(Ordering::SeqCst) != generation
    }

    /// Press a key unless the sequence was cancelled. Checked under the key lock.
    fn press(&self, code: u16, generation: u64) -> InputResult<bool> {
        let mut keys = self.lock_keys();
        if self.cancelled(generation) {
            return Ok(false);
        }
        keys.sink.key(code, 1)?;
        keys.down.insert(code);
        Ok(true)
    }

    fn release(&self, code: u16) -> InputResult<()> {
        let mut keys = self.lock_keys();
        keys.toggled.remove(&code);
        if keys.down.remove(&code) {
            keys.sink.key(code, 0)?;
        }
        Ok(())
    }

    /// Sleep, but stop early if cancelled. Returns false when cancelled.
    fn wait(&self, duration: Duration, generation: u64) -> bool {
        let deadline = Instant::now() + duration;
        loop {
            if self.cancelled(generation) {
                return false;
            }
            let now = Instant::now();
            if now >= deadline {
                return true;
            }
            thread::sleep((deadline - now).min(CANCEL_POLL));
        }
    }

    fn tap(&self, code: u16, hold: Duration, generation: u64) -> InputResult<Outcome> {
        if !self.press(code, generation)? {
            return Ok(Outcome::Cancelled);
        }
        let finished = self.wait(hold, generation);
        self.release(code)?;
        Ok(if finished { Outcome::Completed } else { Outcome::Cancelled })
    }

    fn execute_action(&self, action: &Action, generation: u64) -> InputResult<Outcome> {
        match action {
            Action::KeyPress { key, duration_ms } => {
                if key.trim().is_empty() {
                    return Ok(Outcome::Completed);
                }
                let code = key_code(key)?;
                self.tap(code, Duration::from_millis((*duration_ms).unwrap_or(30)), generation)
            }
            Action::KeyCombo { keys, duration_ms } => {
                let keys = keys
                    .iter()
                    .filter(|key| !key.trim().is_empty())
                    .collect::<Vec<_>>();
                if keys.len() < 2 {
                    return Ok(Outcome::Completed);
                }
                let codes = keys
                    .iter()
                    .map(|key| key_code(key))
                    .collect::<InputResult<Vec<_>>>()?;
                let mut outcome = Outcome::Completed;
                let mut pressed = Vec::new();
                for code in &codes {
                    if !self.press(*code, generation)? {
                        outcome = Outcome::Cancelled;
                        break;
                    }
                    pressed.push(*code);
                }
                if outcome == Outcome::Completed
                    && !self.wait(Duration::from_millis((*duration_ms).unwrap_or(40)), generation)
                {
                    outcome = Outcome::Cancelled;
                }
                for code in pressed.iter().rev() {
                    self.release(*code)?;
                }
                Ok(outcome)
            }
            Action::KeyHold { key } => {
                if key.trim().is_empty() {
                    return Ok(Outcome::Completed);
                }
                let code = key_code(key)?;
                Ok(if self.press(code, generation)? {
                    Outcome::Completed
                } else {
                    Outcome::Cancelled
                })
            }
            Action::KeyRelease { key } => {
                if key.trim().is_empty() {
                    return Ok(Outcome::Completed);
                }
                self.release(key_code(key)?)?;
                Ok(Outcome::Completed)
            }
            Action::KeyToggle { key } => {
                if key.trim().is_empty() {
                    return Ok(Outcome::Completed);
                }
                let code = key_code(key)?;
                let mut keys = self.lock_keys();
                if keys.toggled.remove(&code) {
                    if keys.down.remove(&code) {
                        keys.sink.key(code, 0)?;
                    }
                    return Ok(Outcome::Completed);
                }
                if self.cancelled(generation) {
                    return Ok(Outcome::Cancelled);
                }
                keys.sink.key(code, 1)?;
                keys.down.insert(code);
                keys.toggled.insert(code);
                Ok(Outcome::Completed)
            }
            Action::MouseClick { button, count } => {
                let code = mouse_button_code(button);
                for _ in 0..(*count).unwrap_or(1).max(1) {
                    if self.tap(code, Duration::from_millis(20), generation)? == Outcome::Cancelled
                        || !self.wait(Duration::from_millis(20), generation)
                    {
                        return Ok(Outcome::Cancelled);
                    }
                }
                Ok(Outcome::Completed)
            }
            Action::TypeText { text } => {
                for character in text.chars() {
                    let Some(keys) = char_to_keys(character) else {
                        continue;
                    };
                    let outcome = if keys.len() == 1 {
                        self.tap(key_code(keys[0])?, Duration::from_millis(12), generation)?
                    } else {
                        let modifier = key_code(keys[0])?;
                        let key = key_code(keys[1])?;
                        if !self.press(modifier, generation)? {
                            return Ok(Outcome::Cancelled);
                        }
                        let outcome = self.tap(key, Duration::from_millis(12), generation)?;
                        self.release(modifier)?;
                        outcome
                    };
                    if outcome == Outcome::Cancelled
                        || !self.wait(Duration::from_millis(5), generation)
                    {
                        return Ok(Outcome::Cancelled);
                    }
                }
                Ok(Outcome::Completed)
            }
            Action::Delay { ms } => Ok(if self.wait(Duration::from_millis(*ms), generation) {
                Outcome::Completed
            } else {
                Outcome::Cancelled
            }),
            Action::NomeEmergencyStop
            | Action::NomeStopListening
            | Action::SystemCommand { .. } => Err(InputError(
                "special actions must be handled by the Nome Voice app".to_string(),
            )),
        }
    }
}

#[derive(Debug)]
struct UInputDevice {
    file: File,
}

impl UInputDevice {
    fn new(name: &str, key_codes: &[u16]) -> InputResult<Self> {
        let mut file = OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(UINPUT_PATH)
            .map_err(|error| InputError(format!("could not open {UINPUT_PATH}: {error}")))?;

        ioctl(file.as_raw_fd(), UI_SET_EVBIT, EV_KEY as libc::c_int)?;
        for code in key_codes {
            ioctl(file.as_raw_fd(), UI_SET_KEYBIT, i32::from(*code))?;
        }

        let user_dev = UInputUserDev::new(name);
        write_struct(&mut file, &user_dev)?;
        ioctl(file.as_raw_fd(), UI_DEV_CREATE, 0)?;
        thread::sleep(Duration::from_millis(80));

        Ok(Self { file })
    }

    fn key(&mut self, code: u16, value: i32) -> InputResult<()> {
        self.event(EV_KEY, code, value)?;
        self.event(EV_SYN, SYN_REPORT, 0)
    }

    fn event(&mut self, event_type: u16, code: u16, value: i32) -> InputResult<()> {
        let event = InputEvent {
            time: libc::timeval {
                tv_sec: 0,
                tv_usec: 0,
            },
            event_type,
            code,
            value,
        };
        write_struct(&mut self.file, &event)
    }
}

impl KeySink for UInputDevice {
    fn key(&mut self, code: u16, value: i32) -> InputResult<()> {
        UInputDevice::key(self, code, value)
    }
}

impl Drop for UInputDevice {
    fn drop(&mut self) {
        let _ = unsafe { libc::ioctl(self.file.as_raw_fd(), UI_DEV_DESTROY) };
    }
}

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct UInputUserDev {
    name: [libc::c_char; UINPUT_MAX_NAME_SIZE],
    id: InputId,
    ff_effects_max: u32,
    absmax: [i32; ABS_CNT],
    absmin: [i32; ABS_CNT],
    absfuzz: [i32; ABS_CNT],
    absflat: [i32; ABS_CNT],
}

impl UInputUserDev {
    fn new(name: &str) -> Self {
        let mut user_dev = Self {
            name: [0; UINPUT_MAX_NAME_SIZE],
            id: InputId {
                bustype: BUS_USB,
                vendor: 0x1209,
                product: 0x0001,
                version: 1,
            },
            ff_effects_max: 0,
            absmax: [0; ABS_CNT],
            absmin: [0; ABS_CNT],
            absfuzz: [0; ABS_CNT],
            absflat: [0; ABS_CNT],
        };

        for (index, byte) in name.as_bytes().iter().take(UINPUT_MAX_NAME_SIZE - 1).enumerate() {
            user_dev.name[index] = *byte as libc::c_char;
        }

        user_dev
    }
}

#[repr(C)]
struct InputEvent {
    time: libc::timeval,
    event_type: u16,
    code: u16,
    value: i32,
}

fn ioctl(fd: libc::c_int, request: libc::c_ulong, value: libc::c_int) -> InputResult<()> {
    let result = unsafe { libc::ioctl(fd, request, value) };
    if result < 0 {
        Err(InputError(io::Error::last_os_error().to_string()))
    } else {
        Ok(())
    }
}

fn write_struct<T>(file: &mut File, value: &T) -> InputResult<()> {
    let bytes = unsafe {
        std::slice::from_raw_parts(
            (value as *const T).cast::<u8>(),
            std::mem::size_of::<T>(),
        )
    };
    file.write_all(bytes)?;
    Ok(())
}

fn key_code(key: &str) -> InputResult<u16> {
    let normalized = normalize_key_name(key);
    key_map()
        .get(normalized.as_str())
        .copied()
        .ok_or_else(|| InputError(format!("unknown key: {key}")))
}

fn normalize_key_name(key: &str) -> String {
    let key = key
        .trim()
        .chars()
        .filter(|character| !matches!(character, '-' | ' '))
        .collect::<String>()
        .to_uppercase();
    let alias = match key.as_str() {
        "CTRL" | "CONTROL" => "LEFTCTRL",
        "ALT" => "LEFTALT",
        "SHIFT" => "LEFTSHIFT",
        "SUPER" | "META" | "WIN" => "LEFTMETA",
        "ESCAPE" => "ESC",
        "RETURN" => "ENTER",
        "PGUP" => "PAGEUP",
        "PGDN" => "PAGEDOWN",
        other => other,
    };
    alias.trim_start_matches("KEY_").to_string()
}

fn mouse_button_code(button: &MouseButton) -> u16 {
    match button {
        MouseButton::Left => 272,
        MouseButton::Right => 273,
        MouseButton::Middle => 274,
        MouseButton::Back => 275,
        MouseButton::Forward => 276,
    }
}

fn char_to_keys(character: char) -> Option<&'static [&'static str]> {
    match character {
        'a'..='z' => Some(char_letter(character)),
        'A'..='Z' => Some(char_shift_letter(character)),
        '0' => Some(&["0"]),
        '1' => Some(&["1"]),
        '2' => Some(&["2"]),
        '3' => Some(&["3"]),
        '4' => Some(&["4"]),
        '5' => Some(&["5"]),
        '6' => Some(&["6"]),
        '7' => Some(&["7"]),
        '8' => Some(&["8"]),
        '9' => Some(&["9"]),
        ' ' => Some(&["SPACE"]),
        '\n' => Some(&["ENTER"]),
        '\t' => Some(&["TAB"]),
        '.' => Some(&["DOT"]),
        ',' => Some(&["COMMA"]),
        '-' => Some(&["MINUS"]),
        '=' => Some(&["EQUAL"]),
        '/' => Some(&["SLASH"]),
        '\\' => Some(&["BACKSLASH"]),
        ';' => Some(&["SEMICOLON"]),
        '\'' => Some(&["APOSTROPHE"]),
        '[' => Some(&["LEFTBRACE"]),
        ']' => Some(&["RIGHTBRACE"]),
        '`' => Some(&["GRAVE"]),
        '!' => Some(&["LEFTSHIFT", "1"]),
        '@' => Some(&["LEFTSHIFT", "2"]),
        '#' => Some(&["LEFTSHIFT", "3"]),
        '$' => Some(&["LEFTSHIFT", "4"]),
        '%' => Some(&["LEFTSHIFT", "5"]),
        '^' => Some(&["LEFTSHIFT", "6"]),
        '&' => Some(&["LEFTSHIFT", "7"]),
        '*' => Some(&["LEFTSHIFT", "8"]),
        '(' => Some(&["LEFTSHIFT", "9"]),
        ')' => Some(&["LEFTSHIFT", "0"]),
        '_' => Some(&["LEFTSHIFT", "MINUS"]),
        '+' => Some(&["LEFTSHIFT", "EQUAL"]),
        '?' => Some(&["LEFTSHIFT", "SLASH"]),
        '|' => Some(&["LEFTSHIFT", "BACKSLASH"]),
        ':' => Some(&["LEFTSHIFT", "SEMICOLON"]),
        '"' => Some(&["LEFTSHIFT", "APOSTROPHE"]),
        '{' => Some(&["LEFTSHIFT", "LEFTBRACE"]),
        '}' => Some(&["LEFTSHIFT", "RIGHTBRACE"]),
        '~' => Some(&["LEFTSHIFT", "GRAVE"]),
        '<' => Some(&["LEFTSHIFT", "COMMA"]),
        '>' => Some(&["LEFTSHIFT", "DOT"]),
        _ => None,
    }
}

fn char_letter(character: char) -> &'static [&'static str] {
    match character {
        'a' => &["A"],
        'b' => &["B"],
        'c' => &["C"],
        'd' => &["D"],
        'e' => &["E"],
        'f' => &["F"],
        'g' => &["G"],
        'h' => &["H"],
        'i' => &["I"],
        'j' => &["J"],
        'k' => &["K"],
        'l' => &["L"],
        'm' => &["M"],
        'n' => &["N"],
        'o' => &["O"],
        'p' => &["P"],
        'q' => &["Q"],
        'r' => &["R"],
        's' => &["S"],
        't' => &["T"],
        'u' => &["U"],
        'v' => &["V"],
        'w' => &["W"],
        'x' => &["X"],
        'y' => &["Y"],
        'z' => &["Z"],
        _ => &[],
    }
}

fn char_shift_letter(character: char) -> &'static [&'static str] {
    match character {
        'A' => &["LEFTSHIFT", "A"],
        'B' => &["LEFTSHIFT", "B"],
        'C' => &["LEFTSHIFT", "C"],
        'D' => &["LEFTSHIFT", "D"],
        'E' => &["LEFTSHIFT", "E"],
        'F' => &["LEFTSHIFT", "F"],
        'G' => &["LEFTSHIFT", "G"],
        'H' => &["LEFTSHIFT", "H"],
        'I' => &["LEFTSHIFT", "I"],
        'J' => &["LEFTSHIFT", "J"],
        'K' => &["LEFTSHIFT", "K"],
        'L' => &["LEFTSHIFT", "L"],
        'M' => &["LEFTSHIFT", "M"],
        'N' => &["LEFTSHIFT", "N"],
        'O' => &["LEFTSHIFT", "O"],
        'P' => &["LEFTSHIFT", "P"],
        'Q' => &["LEFTSHIFT", "Q"],
        'R' => &["LEFTSHIFT", "R"],
        'S' => &["LEFTSHIFT", "S"],
        'T' => &["LEFTSHIFT", "T"],
        'U' => &["LEFTSHIFT", "U"],
        'V' => &["LEFTSHIFT", "V"],
        'W' => &["LEFTSHIFT", "W"],
        'X' => &["LEFTSHIFT", "X"],
        'Y' => &["LEFTSHIFT", "Y"],
        'Z' => &["LEFTSHIFT", "Z"],
        _ => &[],
    }
}

fn virtual_key_codes() -> Vec<u16> {
    let mut codes = key_map().values().copied().collect::<Vec<_>>();
    codes.extend([272, 273, 274, 275, 276]);
    codes.sort_unstable();
    codes.dedup();
    codes
}

fn key_map() -> HashMap<&'static str, u16> {
    HashMap::from([
        ("ESC", 1),
        ("1", 2),
        ("2", 3),
        ("3", 4),
        ("4", 5),
        ("5", 6),
        ("6", 7),
        ("7", 8),
        ("8", 9),
        ("9", 10),
        ("0", 11),
        ("MINUS", 12),
        ("EQUAL", 13),
        ("BACKSPACE", 14),
        ("TAB", 15),
        ("Q", 16),
        ("W", 17),
        ("E", 18),
        ("R", 19),
        ("T", 20),
        ("Y", 21),
        ("U", 22),
        ("I", 23),
        ("O", 24),
        ("P", 25),
        ("LEFTBRACE", 26),
        ("RIGHTBRACE", 27),
        ("ENTER", 28),
        ("LEFTCTRL", 29),
        ("A", 30),
        ("S", 31),
        ("D", 32),
        ("F", 33),
        ("G", 34),
        ("H", 35),
        ("J", 36),
        ("K", 37),
        ("L", 38),
        ("SEMICOLON", 39),
        ("APOSTROPHE", 40),
        ("GRAVE", 41),
        ("LEFTSHIFT", 42),
        ("BACKSLASH", 43),
        ("Z", 44),
        ("X", 45),
        ("C", 46),
        ("V", 47),
        ("B", 48),
        ("N", 49),
        ("M", 50),
        ("COMMA", 51),
        ("DOT", 52),
        ("SLASH", 53),
        ("RIGHTSHIFT", 54),
        ("KPASTERISK", 55),
        ("LEFTALT", 56),
        ("SPACE", 57),
        ("CAPSLOCK", 58),
        ("F1", 59),
        ("F2", 60),
        ("F3", 61),
        ("F4", 62),
        ("F5", 63),
        ("F6", 64),
        ("F7", 65),
        ("F8", 66),
        ("F9", 67),
        ("F10", 68),
        ("NUMLOCK", 69),
        ("SCROLLLOCK", 70),
        ("KP7", 71),
        ("KP8", 72),
        ("KP9", 73),
        ("KPMINUS", 74),
        ("KP4", 75),
        ("KP5", 76),
        ("KP6", 77),
        ("KPPLUS", 78),
        ("KP1", 79),
        ("KP2", 80),
        ("KP3", 81),
        ("KP0", 82),
        ("KPDOT", 83),
        ("F11", 87),
        ("F12", 88),
        ("KPENTER", 96),
        ("RIGHTCTRL", 97),
        ("KPSLASH", 98),
        ("SYSRQ", 99),
        ("RIGHTALT", 100),
        ("HOME", 102),
        ("UP", 103),
        ("PAGEUP", 104),
        ("LEFT", 105),
        ("RIGHT", 106),
        ("END", 107),
        ("DOWN", 108),
        ("PAGEDOWN", 109),
        ("INSERT", 110),
        ("DELETE", 111),
        ("MUTE", 113),
        ("VOLUMEDOWN", 114),
        ("VOLUMEUP", 115),
        ("KPEQUAL", 117),
        ("PAUSE", 119),
        ("LEFTMETA", 125),
        ("RIGHTMETA", 126),
        ("PLAYPAUSE", 164),
        ("PREVIOUSSONG", 165),
        ("NEXTSONG", 163),
        ("F13", 183),
        ("F14", 184),
        ("F15", 185),
        ("F16", 186),
        ("F17", 187),
        ("F18", 188),
        ("F19", 189),
        ("F20", 190),
        ("F21", 191),
        ("F22", 192),
        ("F23", 193),
        ("F24", 194),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[derive(Clone, Default)]
    struct RecordingSink(Arc<Mutex<Vec<(u16, i32)>>>);

    impl KeySink for RecordingSink {
        fn key(&mut self, code: u16, value: i32) -> InputResult<()> {
            self.0.lock().unwrap().push((code, value));
            Ok(())
        }
    }

    fn executor() -> (Arc<ActionExecutor>, RecordingSink) {
        let sink = RecordingSink::default();
        (Arc::new(ActionExecutor::with_sink(Box::new(sink.clone()))), sink)
    }

    fn run_in_background(
        executor: &Arc<ActionExecutor>,
        actions: Vec<Action>,
    ) -> thread::JoinHandle<(Outcome, Duration)> {
        let executor = Arc::clone(executor);
        thread::spawn(move || {
            let started = Instant::now();
            let outcome = executor.execute(&actions).unwrap();
            (outcome, started.elapsed())
        })
    }

    fn hold(key: &str) -> Action {
        Action::KeyHold { key: key.to_string() }
    }

    fn delay(ms: u64) -> Action {
        Action::Delay { ms }
    }

    #[test]
    fn release_all_interrupts_a_running_delay_quickly() {
        let (executor, sink) = executor();
        let running = run_in_background(&executor, vec![hold("W"), delay(3000), hold("S")]);
        thread::sleep(Duration::from_millis(100));
        let stop_started = Instant::now();
        executor.release_all().unwrap();
        let stop_took = stop_started.elapsed();
        let (outcome, total) = running.join().unwrap();

        assert_eq!(outcome, Outcome::Cancelled);
        assert!(stop_took < Duration::from_millis(50), "stop took {stop_took:?}");
        assert!(total < Duration::from_millis(500), "sequence took {total:?}");
        assert_eq!(executor.held_key_count(), 0);
        let events = sink.0.lock().unwrap().clone();
        assert_eq!(events, vec![(17, 1), (17, 0)], "W pressed then released, S never pressed");
    }

    #[test]
    fn release_all_lets_go_of_combo_keys_mid_press() {
        let (executor, sink) = executor();
        let running = run_in_background(
            &executor,
            vec![Action::KeyCombo {
                keys: vec!["LEFTCTRL".to_string(), "M".to_string()],
                duration_ms: Some(2000),
            }],
        );
        thread::sleep(Duration::from_millis(100));
        executor.release_all().unwrap();
        assert_eq!(running.join().unwrap().0, Outcome::Cancelled);
        let events = sink.0.lock().unwrap().clone();
        let downs = events.iter().filter(|(_, value)| *value == 1).count();
        let ups = events.iter().filter(|(_, value)| *value == 0).count();
        assert_eq!((downs, ups), (2, 2), "{events:?}");
        assert_eq!(executor.held_key_count(), 0);
    }

    #[test]
    fn release_all_cancels_sequences_waiting_in_line() {
        let (executor, sink) = executor();
        let first = run_in_background(&executor, vec![delay(2000)]);
        thread::sleep(Duration::from_millis(50));
        let queued = run_in_background(&executor, vec![hold("Q")]);
        thread::sleep(Duration::from_millis(50));
        executor.release_all().unwrap();
        assert_eq!(first.join().unwrap().0, Outcome::Cancelled);
        assert_eq!(queued.join().unwrap().0, Outcome::Cancelled);
        assert!(sink.0.lock().unwrap().is_empty());
    }

    #[test]
    fn new_commands_run_normally_after_a_stop() {
        let (executor, sink) = executor();
        executor.release_all().unwrap();
        let outcome = executor
            .execute(&[Action::KeyPress { key: "R".to_string(), duration_ms: Some(1) }])
            .unwrap();
        assert_eq!(outcome, Outcome::Completed);
        assert_eq!(sink.0.lock().unwrap().clone(), vec![(19, 1), (19, 0)]);
    }

    #[test]
    fn toggle_and_release_track_held_keys() {
        let (executor, sink) = executor();
        let toggle = Action::KeyToggle { key: "SHIFT".to_string() };
        executor.execute(&[toggle.clone()]).unwrap();
        assert_eq!(executor.held_key_count(), 1);
        executor.execute(&[toggle]).unwrap();
        assert_eq!(executor.held_key_count(), 0);
        executor.execute(&[hold("E"), Action::KeyRelease { key: "E".to_string() }]).unwrap();
        assert_eq!(executor.held_key_count(), 0);
        assert_eq!(
            sink.0.lock().unwrap().clone(),
            vec![(42, 1), (42, 0), (18, 1), (18, 0)]
        );
    }

    #[test]
    fn type_text_presses_shift_for_capitals() {
        let (executor, sink) = executor();
        executor.execute(&[Action::TypeText { text: "Hi".to_string() }]).unwrap();
        assert_eq!(
            sink.0.lock().unwrap().clone(),
            vec![(42, 1), (35, 1), (35, 0), (42, 0), (23, 1), (23, 0)]
        );
    }
}
