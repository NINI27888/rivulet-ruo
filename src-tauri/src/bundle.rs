use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(rivulet_embedded_bundle)]
const ZIP: &[u8] = include_bytes!("../assets/bundle.zip");
#[cfg(not(rivulet_embedded_bundle))]
const ZIP: &[u8] = &[];
const STAMP: &str = match option_env!("RIVULET_BUNDLE_SHA") {
    Some(value) => value,
    None => "",
};
const PROGRAM: &[&str] = &["runtime", "core", "services", "fatfish"];
const USER_DATA: &[&str] = &[
    "core/data",
    "services/data",
    "services/memory/data",
    "services/bili.json",
    "services/mood.json",
    "services/persona.json",
    "services/persona.txt",
    "services/qq.json",
    "fatfish/data",
    "fatfish/NapCat/napcat/config",
    "fatfish/NapCat/napcat/data",
];
const REQUIRED: &[&str] = &[
    "runtime/python.exe",
    "runtime/Lib/site-packages/segno/__init__.py",
    "runtime/Lib/site-packages/websockets/__init__.py",
    "core/main.py",
    "core/desktop_services.py",
    "services/bili_service.py",
    "services/napcat.py",
    "services/qq_service.py",
    "services/setup_service.py",
    "services/memory/api.py",
    "services/workspace/api.py",
    "services/workspace/provider_llm.py",
    "fatfish/NapCat/node.exe",
    "fatfish/NapCat/index.js",
    "fatfish/bili/qr_login.py",
];

pub fn embedded() -> bool {
    !ZIP.is_empty()
}

pub fn preflight(root: &Path) -> Result<(), String> {
    if PROGRAM.iter().any(|path| root.join(path).exists())
        && !root.join(".bundle-stamp").is_file()
        && ![
            "runtime/python.exe",
            "core/main.py",
            "services/setup_service.py",
            "fatfish/NapCat/node.exe",
        ]
        .iter()
        .all(|file| root.join(file).is_file())
    {
        return Err("当前文件夹包含其他程序目录，请把若的 exe 放到一个单独的文件夹再打开。".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        for path in PROGRAM.iter().copied().chain(USER_DATA.iter().copied()) {
            let mut current = root.to_path_buf();
            for part in Path::new(path).components() {
                current.push(part);
                if fs::symlink_metadata(&current)
                    .is_ok_and(|meta| meta.file_attributes() & 0x400 != 0)
                {
                    return Err(format!(
                        "安装目录包含重定向路径 {path}，请查看该路径后再升级。"
                    ));
                }
            }
        }
    }
    if fs::read_to_string(root.join(".bundle-stamp"))
        .ok()
        .as_deref()
        != Some(STAMP)
    {
        let entries = fs::read_dir(root).map_err(|e| e.to_string())?;
        for entry in entries.flatten() {
            if entry
                .file_name()
                .to_string_lossy()
                .starts_with(".rivulet-install-")
                && fs::read_dir(entry.path().join("previous"))
                    .is_ok_and(|mut old| old.next().is_some())
            {
                return Err(format!(
                    "上次升级有未恢复的备份，保存于 {}。",
                    entry.path().display()
                ));
            }
        }
    }
    Ok(())
}

fn missing(root: &Path) -> Option<&'static str> {
    REQUIRED
        .iter()
        .copied()
        .find(|file| !root.join(file).is_file())
}

#[cfg(windows)]
fn hide(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000);
}

fn unpack(zip: &Path, files: &Path) -> Result<(), String> {
    let mut tar = Command::new("tar");
    tar.arg("-xf").arg(zip).arg("-C").arg(files);
    #[cfg(windows)]
    hide(&mut tar);
    if matches!(tar.status(), Ok(status) if status.success()) {
        return Ok(());
    }
    #[cfg(windows)]
    {
        let mut powershell = Command::new("powershell.exe");
        powershell.args([
            "-NoProfile", "-NonInteractive", "-Command",
            "Expand-Archive -LiteralPath $env:RIVULET_ARCHIVE -DestinationPath $env:RIVULET_UNPACK_TO -Force -ErrorAction Stop",
        ]).env("RIVULET_ARCHIVE", zip).env("RIVULET_UNPACK_TO", files);
        hide(&mut powershell);
        if matches!(powershell.status(), Ok(status) if status.success()) {
            return Ok(());
        }
    }
    Err("内置运行库解压失败。".into())
}

fn move_file(
    moves: &mut Vec<(PathBuf, PathBuf)>,
    from: PathBuf,
    to: PathBuf,
) -> Result<(), String> {
    fs::rename(&from, &to).map_err(|e| format!("无法安装 {}：{e}", from.display()))?;
    moves.push((from, to));
    Ok(())
}

fn install(root: &Path, stage: &Path) -> Result<(), String> {
    let old = stage.join("previous");
    let files = stage.join("files");
    fs::create_dir(&old).map_err(|e| e.to_string())?;
    fs::create_dir(&files).map_err(|e| e.to_string())?;
    let zip = stage.join("bundle.zip");
    fs::write(&zip, ZIP).map_err(|e| e.to_string())?;
    unpack(&zip, &files)?;
    if let Some(file) = missing(&files) {
        return Err(format!("内置运行库缺少 {file}。"));
    }

    let stamp = ".bundle-stamp";
    fs::write(files.join(stamp), STAMP).map_err(|e| e.to_string())?;
    let mut moves = Vec::new();
    let installed = (|| {
        for path in PROGRAM.iter().copied().chain(["使用说明.txt", stamp]) {
            if root.join(path).exists() && files.join(path).exists() {
                move_file(&mut moves, root.join(path), old.join(path))?;
            }
        }
        for path in USER_DATA.iter().copied() {
            let previous = old.join(path);
            if !previous.exists() {
                continue;
            }
            let destination = files.join(path);
            if destination.exists() {
                return Err(format!("安装包包含用户数据路径 {path}。"));
            }
            if let Some(parent) = destination.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            move_file(&mut moves, previous, destination)?;
        }
        for path in PROGRAM.iter().copied().chain(["使用说明.txt", stamp]) {
            if files.join(path).exists() {
                move_file(&mut moves, files.join(path), root.join(path))?;
            }
        }
        Ok::<(), String>(())
    })();

    if let Err(error) = installed {
        for (from, to) in moves.into_iter().rev() {
            if let Err(rollback) = fs::rename(&to, &from) {
                return Err(format!(
                    "{error}。回滚未完成（{rollback}），备份在 {}。",
                    stage.display()
                ));
            }
        }
        return Err(error);
    }
    Ok(())
}

pub fn ensure(root: &Path) -> Result<(), String> {
    if fs::read_to_string(root.join(".bundle-stamp"))
        .ok()
        .as_deref()
        == Some(STAMP)
        && missing(root).is_none()
    {
        return Ok(());
    }
    let stage = root.join(format!(".rivulet-install-{}", std::process::id()));
    fs::create_dir(&stage).map_err(|e| format!("无法准备安装目录。{e}"))?;
    install(root, &stage)?;
    let _ = fs::remove_dir_all(&stage);
    Ok(())
}
