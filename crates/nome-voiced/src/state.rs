use std::sync::Arc;

use crate::input::ActionExecutor;
use serde::Serialize;
use nome_voice_core::{CommandMatch, PhraseMatcher, Profile};

#[derive(Debug)]
pub struct DaemonState {
    active_profile: Option<Profile>,
    matcher: Option<PhraseMatcher>,
    input: InputManager,
    listening: bool,
    last_heard: Option<String>,
    last_command_fired: Option<FiredCommand>,
}

impl Default for DaemonState {
    fn default() -> Self {
        Self {
            active_profile: None,
            matcher: None,
            input: InputManager::default(),
            listening: false,
            last_heard: None,
            last_command_fired: None,
        }
    }
}

impl DaemonState {
    pub fn snapshot(&self) -> StatusSnapshot {
        StatusSnapshot {
            listening: self.listening,
            active_profile: self.active_profile.as_ref().map(|profile| ActiveProfile {
                id: profile.id.clone(),
                name: profile.name.clone(),
                command_count: profile.commands.len(),
            }),
            last_heard: self.last_heard.clone(),
            last_command_fired: self.last_command_fired.clone(),
            input_available: self.input.is_available(),
            speech_available: false,
            dry_run: self.input.dry_run,
            held_keys: self.input.held_key_count(),
        }
    }

    pub fn setup_check(&mut self) -> StatusSnapshot {
        let _ = self.input.ensure();
        self.snapshot()
    }

    pub fn activate_profile(
        &mut self,
        profile: Profile,
    ) -> Result<StatusSnapshot, nome_voice_core::CoreError> {
        let matcher = PhraseMatcher::from_profile(&profile)?;
        self.active_profile = Some(profile);
        self.matcher = Some(matcher);
        self.listening = false;
        self.last_heard = None;
        self.last_command_fired = None;
        Ok(self.snapshot())
    }

    pub fn set_listening(&mut self, listening: bool) -> StatusSnapshot {
        self.listening = listening;
        self.snapshot()
    }

    pub fn test_recognition(&mut self, text: String) -> RecognitionTestResult {
        self.last_heard = Some(text.clone());

        let matched = self
            .matcher
            .as_ref()
            .and_then(|matcher| matcher.find(&text))
            .map(FiredCommand::from_match);

        if let Some(command) = &matched {
            self.last_command_fired = Some(command.clone());
        }

        RecognitionTestResult {
            text,
            matched,
            listening: self.listening,
        }
    }

    /// The shared executor. Callers run actions on it without holding the state lock,
    /// so an emergency stop never waits behind a running macro.
    pub fn input_executor(&mut self) -> Result<Arc<ActionExecutor>, String> {
        self.input.ensure()
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StatusSnapshot {
    pub listening: bool,
    pub active_profile: Option<ActiveProfile>,
    pub last_heard: Option<String>,
    pub last_command_fired: Option<FiredCommand>,
    pub input_available: bool,
    pub speech_available: bool,
    pub dry_run: bool,
    pub held_keys: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActiveProfile {
    pub id: String,
    pub name: String,
    pub command_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FiredCommand {
    pub command_id: String,
    pub command_name: String,
    pub phrase: String,
}

impl FiredCommand {
    fn from_match(command_match: &CommandMatch) -> Self {
        Self {
            command_id: command_match.command_id.clone(),
            command_name: command_match.command_name.clone(),
            phrase: command_match.phrase.clone(),
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecognitionTestResult {
    pub text: String,
    pub matched: Option<FiredCommand>,
    pub listening: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecuteActionsResult {
    pub executed_action_count: usize,
    pub cancelled: bool,
}

#[derive(Debug)]
struct InputManager {
    executor: Option<Arc<ActionExecutor>>,
    last_error: Option<String>,
    /// NOME_VOICED_DRY_RUN=1: accept actions but send no events (for testing).
    dry_run: bool,
}

impl Default for InputManager {
    fn default() -> Self {
        Self {
            executor: None,
            last_error: None,
            dry_run: std::env::var("NOME_VOICED_DRY_RUN").is_ok_and(|value| value == "1"),
        }
    }
}

impl InputManager {
    fn ensure(&mut self) -> Result<Arc<ActionExecutor>, String> {
        if let Some(executor) = &self.executor {
            return Ok(Arc::clone(executor));
        }
        let created = if self.dry_run {
            Ok(ActionExecutor::dry_run())
        } else {
            ActionExecutor::new()
        };
        match created {
            Ok(executor) => {
                let executor = Arc::new(executor);
                self.executor = Some(Arc::clone(&executor));
                self.last_error = None;
                Ok(executor)
            }
            Err(error) => {
                let message = error.to_string();
                self.last_error = Some(message.clone());
                Err(message)
            }
        }
    }

    fn is_available(&self) -> bool {
        self.executor.is_some()
    }

    fn held_key_count(&self) -> usize {
        self.executor
            .as_ref()
            .map_or(0, |executor| executor.held_key_count())
    }
}
