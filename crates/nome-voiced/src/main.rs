mod input;
mod protocol;
mod state;

use std::{
    env,
    error::Error,
    path::{Path, PathBuf},
    sync::Arc,
};

use protocol::{
    CommandTestParams, ExecuteActionsParams, ListeningSetParams, ProfileActivateParams, Request,
    Response,
};
use state::DaemonState;
use tokio::{
    fs,
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream},
    sync::Mutex,
};
use nome_voice_core::Profile;
use tracing::{error, info};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    set_process_name("nome-voiced");
    tracing_subscriber::fmt::init();

    let socket_path = socket_path();
    prepare_socket_path(&socket_path).await?;

    let listener = UnixListener::bind(&socket_path)?;
    let state = Arc::new(Mutex::new(DaemonState::default()));

    info!(path = %socket_path.display(), "nome-voiced listening");
    println!("nome-voiced listening on {}", socket_path.display());

    loop {
        let (stream, _) = listener.accept().await?;
        let state = Arc::clone(&state);
        tokio::spawn(async move {
            if let Err(err) = handle_client(stream, state).await {
                error!(%err, "client connection failed");
            }
        });
    }
}

async fn handle_client(
    stream: UnixStream,
    state: Arc<Mutex<DaemonState>>,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }

        let response = match serde_json::from_str::<Request>(&line) {
            Ok(request) => dispatch(request, Arc::clone(&state)).await,
            Err(err) => Response::error(None, "invalid_json", err.to_string()),
        };

        let encoded = serde_json::to_string(&response)?;
        writer.write_all(encoded.as_bytes()).await?;
        writer.write_all(b"\n").await?;
    }

    Ok(())
}

#[cfg(target_os = "linux")]
fn set_process_name(name: &str) {
    if let Ok(name) = std::ffi::CString::new(name) {
        unsafe {
            libc::prctl(libc::PR_SET_NAME, name.as_ptr(), 0, 0, 0);
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn set_process_name(_name: &str) {}

async fn dispatch(request: Request, state: Arc<Mutex<DaemonState>>) -> Response {
    let id = request.id.clone();

    match request.method.as_str() {
        "status.get" => {
            let state = state.lock().await;
            Response::ok(id, state.snapshot())
        }
        "listening.set" => {
            let params = match serde_json::from_value::<ListeningSetParams>(request.params) {
                Ok(params) => params,
                Err(err) => return Response::error(id, "invalid_params", err.to_string()),
            };
            let mut state = state.lock().await;
            Response::ok(id, state.set_listening(params.listening))
        }
        "profile.activate" => {
            let params = match serde_json::from_value::<ProfileActivateParams>(request.params) {
                Ok(params) => params,
                Err(err) => return Response::error(id, "invalid_params", err.to_string()),
            };
            let profile = match Profile::from_value(params.profile) {
                Ok(profile) => profile,
                Err(err) => return Response::error(id, "invalid_profile", err.to_string()),
            };
            let mut state = state.lock().await;
            match state.activate_profile(profile) {
                Ok(snapshot) => Response::ok(id, snapshot),
                Err(err) => Response::error(id, "invalid_profile", err.to_string()),
            }
        }
        "command.test" => {
            let params = match serde_json::from_value::<CommandTestParams>(request.params) {
                Ok(params) => params,
                Err(err) => return Response::error(id, "invalid_params", err.to_string()),
            };
            let mut state = state.lock().await;
            Response::ok(id, state.test_recognition(params.text))
        }
        "actions.execute" => {
            let params = match serde_json::from_value::<ExecuteActionsParams>(request.params) {
                Ok(params) => params,
                Err(err) => return Response::error(id, "invalid_params", err.to_string()),
            };
            let mut state = state.lock().await;
            match state.execute_actions(params.actions) {
                Ok(result) => Response::ok(id, result),
                Err(err) => Response::error(id, "input_error", err),
            }
        }
        "actions.releaseAll" => {
            let mut state = state.lock().await;
            match state.release_all() {
                Ok(result) => Response::ok(id, result),
                Err(err) => Response::error(id, "input_error", err),
            }
        }
        "setup.check" => {
            let mut state = state.lock().await;
            Response::ok(id, state.setup_check())
        }
        _ => Response::error(
            id,
            "unknown_method",
            format!("unknown method '{}'", request.method),
        ),
    }
}

fn socket_path() -> PathBuf {
    let runtime_dir = env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| env::temp_dir());
    runtime_dir.join("nome-voice").join("nome-voiced.sock")
}

async fn prepare_socket_path(socket_path: &Path) -> Result<(), Box<dyn Error>> {
    if let Some(parent) = socket_path.parent() {
        fs::create_dir_all(parent).await?;
    }

    if fs::try_exists(socket_path).await? {
        fs::remove_file(socket_path).await?;
    }

    Ok(())
}
