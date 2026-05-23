use thiserror::Error;

pub type CoreResult<T> = Result<T, CoreError>;

#[derive(Debug, Error)]
pub enum CoreError {
    #[error("profile JSON is invalid: {0}")]
    InvalidJson(#[from] serde_json::Error),

    #[error("unsupported profile schema version {0}")]
    UnsupportedSchemaVersion(u16),

    #[error("profile validation failed at {field}: {message}")]
    Validation { field: String, message: String },

    #[error("duplicate command id '{0}'")]
    DuplicateCommandId(String),

    #[error("command '{command}' has duplicate phrase '{phrase}'")]
    DuplicatePhrase { command: String, phrase: String },

    #[error("phrase '{phrase}' is used by both '{first_command}' and '{second_command}'")]
    PhraseConflict {
        phrase: String,
        first_command: String,
        second_command: String,
    },
}
