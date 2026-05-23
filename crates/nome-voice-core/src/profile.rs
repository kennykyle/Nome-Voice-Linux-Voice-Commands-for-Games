use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use crate::{matcher::normalize_phrase, CoreError, CoreResult};

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub schema_version: u16,
    pub id: String,
    pub name: String,
    pub target: Option<Target>,
    pub activation: Activation,
    pub speech: Speech,
    pub commands: Vec<Command>,
}

impl Profile {
    pub fn from_json(input: &str) -> CoreResult<Self> {
        let profile: Self = serde_json::from_str(input)?;
        Self::from_parsed(profile)
    }

    pub fn from_value(input: serde_json::Value) -> CoreResult<Self> {
        let profile: Self = serde_json::from_value(input)?;
        Self::from_parsed(profile)
    }

    fn from_parsed(profile: Self) -> CoreResult<Self> {
        if profile.schema_version != 1 && profile.schema_version != 2 {
            return Err(CoreError::UnsupportedSchemaVersion(profile.schema_version));
        }
        profile.validate()?;
        Ok(profile)
    }

    pub fn validate(&self) -> CoreResult<()> {
        require_non_empty("id", &self.id)?;
        require_non_empty("name", &self.name)?;

        if let Some(target) = &self.target {
            target.validate()?;
        }

        self.activation.validate()?;
        self.speech.validate()?;

        let mut command_ids = HashSet::new();
        for command in &self.commands {
            command.validate()?;
            if !command_ids.insert(command.id.as_str()) {
                return Err(CoreError::DuplicateCommandId(command.id.clone()));
            }
        }

        Ok(())
    }

    pub fn command_by_id(&self, command_id: &str) -> Option<&Command> {
        self.commands.iter().find(|command| command.id == command_id)
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Target {
    pub kind: TargetKind,
    pub process_name: Option<String>,
}

impl Target {
    fn validate(&self) -> CoreResult<()> {
        if matches!(self.kind, TargetKind::Process) {
            let process_name = self.process_name.as_deref().unwrap_or_default();
            require_non_empty("target.processName", process_name)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetKind {
    Manual,
    Process,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Activation {
    pub mode: ActivationMode,
    pub hotkey: Option<String>,
}

impl Activation {
    fn validate(&self) -> CoreResult<()> {
        match self.mode {
            ActivationMode::PushToTalk | ActivationMode::Toggle => {
                let hotkey = self.hotkey.as_deref().unwrap_or_default();
                require_non_empty("activation.hotkey", hotkey)?;
            }
            ActivationMode::Always => {}
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ActivationMode {
    PushToTalk,
    Toggle,
    Always,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Speech {
    pub engine: SpeechEngine,
    pub language: String,
    pub model_path: Option<String>,
    pub min_confidence: Option<f32>,
}

impl Speech {
    fn validate(&self) -> CoreResult<()> {
        require_non_empty("speech.language", &self.language)?;

        if let Some(model_path) = &self.model_path {
            require_non_empty("speech.modelPath", model_path)?;
        }

        if let Some(min_confidence) = self.min_confidence {
            if !(0.0..=1.0).contains(&min_confidence) {
                return validation_error("speech.minConfidence", "must be between 0 and 1");
            }
        }

        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SpeechEngine {
    Vosk,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Command {
    pub id: String,
    pub name: String,
    pub phrases: Vec<String>,
    pub actions: Vec<Action>,
}

impl Command {
    fn validate(&self) -> CoreResult<()> {
        require_non_empty("commands[].id", &self.id)?;
        require_non_empty("commands[].name", &self.name)?;

        for phrase in &self.phrases {
            if normalize_phrase(phrase).is_empty() {
                continue;
            }
        }

        for action in &self.actions {
            action.validate()?;
        }

        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", rename_all_fields = "camelCase")]
pub enum Action {
    KeyPress {
        key: String,
        duration_ms: Option<u64>,
    },
    KeyCombo {
        keys: Vec<String>,
        duration_ms: Option<u64>,
    },
    KeyHold {
        key: String,
    },
    KeyRelease {
        key: String,
    },
    KeyToggle {
        key: String,
    },
    MouseClick {
        button: MouseButton,
        count: Option<u8>,
    },
    TypeText {
        text: String,
    },
    Delay {
        ms: u64,
    },
    NomeEmergencyStop,
    NomeStopListening,
    SystemCommand {
        command: String,
        timeout_ms: Option<u64>,
    },
}

impl Action {
    fn validate(&self) -> CoreResult<()> {
        match self {
            Action::KeyPress { .. } => {}
            Action::KeyCombo { keys, duration_ms } => {
                for key in keys {
                    if key.trim().is_empty() {
                        continue;
                    }
                }
                let _ = duration_ms;
            }
            Action::KeyHold { .. } | Action::KeyRelease { .. } | Action::KeyToggle { .. } => {}
            Action::MouseClick { .. } => {}
            Action::TypeText { .. } => {}
            Action::Delay { .. } => {}
            Action::NomeEmergencyStop | Action::NomeStopListening => {}
            Action::SystemCommand { command, timeout_ms } => {
                let _ = command;
                let _ = timeout_ms;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
    Back,
    Forward,
}

fn require_non_empty(field: &str, value: &str) -> CoreResult<()> {
    if value.trim().is_empty() {
        return validation_error(field, "must not be empty");
    }
    Ok(())
}

fn validation_error<T>(field: &str, message: &str) -> CoreResult<T> {
    Err(CoreError::Validation {
        field: field.to_string(),
        message: message.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_example_profile() {
        let profile = Profile::from_json(include_str!("../../../examples/profile.example.json"))
            .expect("example profile should load");

        assert_eq!(profile.name, "Example Game");
        assert_eq!(profile.commands.len(), 3);
    }

    #[test]
    fn rejects_activation_hotkey_missing_for_push_to_talk() {
        let error = Profile::from_json(
            r#"{
              "schemaVersion": 1,
              "id": "example",
              "name": "Example",
              "activation": { "mode": "push_to_talk" },
              "speech": { "engine": "vosk", "language": "en-US" },
              "commands": []
            }"#,
        )
        .unwrap_err();

        assert!(matches!(error, CoreError::Validation { .. }));
    }

    #[test]
    fn accepts_incomplete_key_combo_for_editor_saves() {
        let profile = Profile::from_json(
            r#"{
              "schemaVersion": 1,
              "id": "example",
              "name": "Example",
              "activation": { "mode": "always" },
              "speech": { "engine": "vosk", "language": "en-US" },
              "commands": [
                {
                  "id": "bad",
                  "name": "Bad",
                  "phrases": ["bad"],
                  "actions": [{ "type": "key_combo", "keys": ["M"] }]
                }
              ]
            }"#,
        )
        .expect("incomplete editor state should still save");

        assert_eq!(profile.commands.len(), 1);
    }
}
