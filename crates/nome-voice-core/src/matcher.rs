use std::collections::{HashMap, HashSet};

use crate::{CoreError, CoreResult, Profile};

#[derive(Debug, Clone)]
pub struct CommandMatch {
    pub command_id: String,
    pub command_name: String,
    pub phrase: String,
}

#[derive(Debug, Clone)]
pub struct PhraseMatcher {
    phrases: HashMap<String, CommandMatch>,
}

impl PhraseMatcher {
    pub fn from_profile(profile: &Profile) -> CoreResult<Self> {
        let mut phrases = HashMap::new();

        for command in &profile.commands {
            let mut command_phrases = HashSet::new();

            for phrase in &command.phrases {
                let normalized = normalize_phrase(phrase);
                if !command_phrases.insert(normalized.clone()) {
                    return Err(CoreError::DuplicatePhrase {
                        command: command.name.clone(),
                        phrase: normalized,
                    });
                }

                let command_match = CommandMatch {
                    command_id: command.id.clone(),
                    command_name: command.name.clone(),
                    phrase: normalized.clone(),
                };

                if let Some(existing) = phrases.insert(normalized.clone(), command_match) {
                    return Err(CoreError::PhraseConflict {
                        phrase: normalized,
                        first_command: existing.command_name,
                        second_command: command.name.clone(),
                    });
                }
            }
        }

        Ok(Self { phrases })
    }

    pub fn find(&self, recognized_text: &str) -> Option<&CommandMatch> {
        let normalized = normalize_phrase(recognized_text);
        self.phrases.get(&normalized)
    }

    pub fn grammar_phrases(&self) -> Vec<String> {
        let mut phrases = self.phrases.keys().cloned().collect::<Vec<_>>();
        phrases.sort();
        phrases
    }
}

pub fn normalize_phrase(input: &str) -> String {
    input
        .chars()
        .map(|ch| {
            if ch.is_alphanumeric() || ch.is_whitespace() {
                ch.to_ascii_lowercase()
            } else {
                ' '
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_spacing_case_and_punctuation() {
        assert_eq!(normalize_phrase("  Open,   MAP! "), "open map");
    }

    #[test]
    fn matches_profile_phrase() {
        let profile = Profile::from_json(
            r#"{
              "schemaVersion": 1,
              "id": "example",
              "name": "Example",
              "activation": { "mode": "always" },
              "speech": { "engine": "vosk", "language": "en-US" },
              "commands": [
                {
                  "id": "reload",
                  "name": "Reload",
                  "phrases": ["reload"],
                  "actions": [{ "type": "key_press", "key": "R" }]
                }
              ]
            }"#,
        )
        .unwrap();
        let matcher = PhraseMatcher::from_profile(&profile).unwrap();

        assert_eq!(matcher.find("Reload").unwrap().command_id, "reload");
    }

    #[test]
    fn rejects_duplicate_phrase_on_same_command() {
        let profile = Profile::from_json(
            r#"{
              "schemaVersion": 1,
              "id": "example",
              "name": "Example",
              "activation": { "mode": "always" },
              "speech": { "engine": "vosk", "language": "en-US" },
              "commands": [
                {
                  "id": "reload",
                  "name": "Reload",
                  "phrases": ["reload", "Reload!"],
                  "actions": [{ "type": "key_press", "key": "R" }]
                }
              ]
            }"#,
        )
        .unwrap();

        let error = PhraseMatcher::from_profile(&profile).unwrap_err();
        assert!(matches!(error, CoreError::DuplicatePhrase { .. }));
    }

    #[test]
    fn rejects_phrase_conflict_across_commands() {
        let profile = Profile::from_json(
            r#"{
              "schemaVersion": 1,
              "id": "example",
              "name": "Example",
              "activation": { "mode": "always" },
              "speech": { "engine": "vosk", "language": "en-US" },
              "commands": [
                {
                  "id": "reload",
                  "name": "Reload",
                  "phrases": ["reload"],
                  "actions": [{ "type": "key_press", "key": "R" }]
                },
                {
                  "id": "load",
                  "name": "Load",
                  "phrases": ["Reload!"],
                  "actions": [{ "type": "key_press", "key": "L" }]
                }
              ]
            }"#,
        )
        .unwrap();

        let error = PhraseMatcher::from_profile(&profile).unwrap_err();
        assert!(matches!(error, CoreError::PhraseConflict { .. }));
    }
}
