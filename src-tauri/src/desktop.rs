use std::path::Path;

#[cfg(windows)]
mod windows {
    use super::Path;
    use std::io::{self, BufRead, BufReader, Write};
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::os::windows::process::CommandExt;
    use std::process::{Child, ChildStdin, Command, Stdio};
    use std::sync::mpsc;
    use std::time::Duration;

    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{CreateMutexW, CREATE_NO_WINDOW};
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

    const LEGACY_SERVICES: &str = include_str!("legacy-services.ps1");

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn report_error(error: &str) {
        let title = wide("Rivulet 若 · 启动失败");
        let message = wide(error);
        unsafe {
            MessageBoxW(
                std::ptr::null_mut(),
                message.as_ptr(),
                title.as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
    }

    pub fn lock_instance() -> Result<OwnedHandle, String> {
        let name = wide(r"Local\RivuletRuoDesktop");
        let mutex = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if mutex.is_null() {
            return Err(format!(
                "无法检查若的运行状态。{}",
                io::Error::last_os_error()
            ));
        }
        let exists = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
        let mutex = unsafe { OwnedHandle::from_raw_handle(mutex) };
        if exists {
            return Err("若已经在运行，请返回已打开的窗口。".into());
        }
        Ok(mutex)
    }

    pub fn retire_legacy(root: &Path) -> Result<(), String> {
        let result = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", LEGACY_SERVICES])
            .env("RIVULET_INSTALL_ROOT", root)
            .env("RIVULET_DESKTOP_PID", std::process::id().to_string())
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("无法检查旧版本的后台服务。{e}"))?;
        let reply = String::from_utf8_lossy(&result.stdout);
        let reply = reply.trim();
        if result.status.success() && reply.starts_with("READY ") {
            return Ok(());
        }
        if reply == "WINDOW" {
            return Err("这个目录里已打开另一版若。请先关闭那个窗口，再打开新版。".into());
        }
        if reply == "PUBLISHING" {
            return Err("旧版 B 站还有投稿任务。请等投稿结束再打开新版。".into());
        }
        if let Some(port) = reply
            .strip_prefix("PORT ")
            .and_then(|s| s.parse::<u16>().ok())
        {
            return Err(format!(
                "本机端口 {port} 被另一程序占用。请退出占用该端口的程序后再打开若。"
            ));
        }
        let detail = String::from_utf8_lossy(&result.stderr);
        let _ = std::fs::write(root.join("startup-services.log"), detail.as_bytes());
        Err("没有完成旧后台服务的检查，请查看程序目录里的 startup-services.log。".into())
    }

    pub struct Services {
        _job: OwnedHandle,
        child: Child,
        input: Option<ChildStdin>,
    }

    impl Drop for Services {
        fn drop(&mut self) {
            // EOF asks the supervisor to exit. Closing the job also stops its
            // descendants, including the bundled QQ gateway, if any remain.
            self.input.take();
            let deadline = std::time::Instant::now() + Duration::from_secs(2);
            while matches!(self.child.try_wait(), Ok(None)) && std::time::Instant::now() < deadline
            {
                std::thread::sleep(Duration::from_millis(30));
            }
        }
    }

    fn create_job() -> io::Result<OwnedHandle> {
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                raw,
                JobObjectExtendedLimitInformation,
                (&info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&info) as u32,
            )
        };
        if configured == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }

    pub fn start_services(root: &Path) -> Result<Services, String> {
        let job = create_job().map_err(|e| format!("无法管理若的本机服务。{e}"))?;
        let log = std::fs::File::create(root.join("startup-services.log"))
            .map_err(|e| format!("无法写入启动日志。{e}"))?;
        let mut child = Command::new(root.join("runtime").join("python.exe"))
            .arg(root.join("core").join("desktop_services.py"))
            .current_dir(root.join("core"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log))
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| format!("无法启动包内 Python。{e}"))?;
        if unsafe { AssignProcessToJobObject(job.as_raw_handle(), child.as_raw_handle()) } == 0 {
            let error = io::Error::last_os_error();
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("无法接管本机服务进程。{error}"));
        }
        let output = child.stdout.take().expect("supervisor stdout is piped");
        let input = child.stdin.take().expect("supervisor stdin is piped");
        let mut services = Services {
            _job: job,
            child,
            input: Some(input),
        };
        services
            .input
            .as_mut()
            .unwrap()
            .write_all(b"start\n")
            .map_err(|e| format!("本机服务未能启动。{e}"))?;

        let (ready, result) = mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            let _ = BufReader::new(output).read_line(&mut line);
            let _ = ready.send(line);
        });
        let status = result
            .recv_timeout(Duration::from_secs(28))
            .unwrap_or_default();
        if status.trim() != "READY" {
            let hint = status.trim().strip_prefix("FAILED ").unwrap_or("");
            return Err(format!(
                "本机服务未就绪（{hint}）。启动详情见 startup-services.log。"
            ));
        }
        Ok(services)
    }
}

#[cfg(windows)]
pub use windows::*;
