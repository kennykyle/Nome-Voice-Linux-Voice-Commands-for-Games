pub mod error;
pub mod matcher;
pub mod profile;

pub use error::{CoreError, CoreResult};
pub use matcher::{normalize_phrase, CommandMatch, PhraseMatcher};
pub use profile::{Action, Activation, ActivationMode, Command, MouseButton, Profile, Speech};
