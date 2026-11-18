// 会话存储:token 用 Windows DPAPI(当前用户)加密后落盘,明文只在内存里活一次。
// 不存密码;没有 token 文件就等于没登录。
use std::fs;
use std::path::PathBuf;

const ENTROPY: &[u8] = b"RivuletRuo/v1/session";
const DEFAULT_API: &str = "http://127.0.0.1:8088";

#[cfg(windows)]
mod dpapi {
    use core::ffi::c_void;

    #[repr(C)]
    struct Blob {
        cb_data: u32,
        pb_data: *mut u8,
    }

    const CRYPTPROTECT_UI_FORBIDDEN: u32 = 0x1;

    #[link(name = "crypt32")]
    extern "system" {
        fn CryptProtectData(
            data_in: *const Blob,
            description: *const u16,
            entropy: *const Blob,
            reserved: *const c_void,
            prompt: *const c_void,
            flags: u32,
            data_out: *mut Blob,
        ) -> i32;
        fn CryptUnprotectData(
            data_in: *const Blob,
            description_out: *mut *mut u16,
            entropy: *const Blob,
            reserved: *const c_void,
            prompt: *const c_void,
            flags: u32,
            data_out: *mut Blob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn LocalFree(mem: *mut c_void) -> *mut c_void;
    }

    fn blob_of(bytes: &mut [u8]) -> Blob {
        Blob { cb_data: bytes.len() as u32, pb_data: bytes.as_mut_ptr() }
    }

    pub fn protect(plain: &[u8]) -> Result<Vec<u8>, String> {
        let mut input = plain.to_vec();
        let mut entropy = super::ENTROPY.to_vec();
        let in_blob = blob_of(&mut input);
        let ent_blob = blob_of(&mut entropy);
        let mut out = Blob { cb_data: 0, pb_data: std::ptr::null_mut() };
        let ok = unsafe {
            CryptProtectData(&in_blob, std::ptr::null(), &ent_blob, std::ptr::null(), std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut out)
        };
        if ok == 0 {
            return Err("系统加密失败(DPAPI)".to_string());
        }
        let bytes = unsafe { std::slice::from_raw_parts(out.pb_data, out.cb_data as usize) }.to_vec();
        unsafe { LocalFree(out.pb_data as *mut c_void) };
        Ok(bytes)
    }

    pub fn unprotect(sealed: &[u8]) -> Result<Vec<u8>, String> {
        let mut input = sealed.to_vec();
        let mut entropy = super::ENTROPY.to_vec();
        let in_blob = blob_of(&mut input);
        let ent_blob = blob_of(&mut entropy);
        let mut out = Blob { cb_data: 0, pb_data: std::ptr::null_mut() };
        let ok = unsafe {
            CryptUnprotectData(&in_blob, std::ptr::null_mut(), &ent_blob, std::ptr::null(), std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut out)
        };
        if ok == 0 {
            return Err("系统解密失败(可能换了 Windows 账号)".to_string());
        }
        let bytes = unsafe { std::slice::from_raw_parts(out.pb_data, out.cb_data as usize) }.to_vec();
        unsafe { LocalFree(out.pb_data as *mut c_void) };
        Ok(bytes)
    }
}

pub struct Store {
    base: PathBuf,
}

impl Store {
    pub fn new() -> Self {
        // 数据放在本程序的目录旁边(便携式),装到别处也不会丢
        let base = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.join("data")))
            .unwrap_or_else(|| PathBuf::from("data"));
        let _ = fs::create_dir_all(&base);
        Self { base }
    }

    fn session_path(&self) -> PathBuf {
        self.base.join("session.bin")
    }

    fn config_path(&self) -> PathBuf {
        self.base.join("config.json")
    }

    pub fn save_token(&self, token: &str) -> Result<(), String> {
        #[cfg(windows)]
        {
            let sealed = dpapi::protect(token.as_bytes())?;
            fs::write(self.session_path(), sealed).map_err(|e| format!("写入登录凭证失败:{}", e))?;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            fs::write(self.session_path(), token.as_bytes()).map_err(|e| format!("写入登录凭证失败:{}", e))?;
            let _ = token;
            Ok(())
        }
    }

    pub fn load_token(&self) -> Option<String> {
        let raw = fs::read(self.session_path()).ok()?;
        #[cfg(windows)]
        {
            let plain = dpapi::unprotect(&raw).ok()?;
            String::from_utf8(plain).ok()
        }
        #[cfg(not(windows))]
        {
            String::from_utf8(raw).ok()
        }
    }

    pub fn clear_token(&self) {
        let _ = fs::remove_file(self.session_path());
    }

    /// 服务器地址:环境变量 > data/config.json 的 api_base > 本机 8088
    pub fn api_base(&self) -> String {
        if let Ok(v) = std::env::var("RIVULET_API") {
            let v = v.trim().to_string();
            if !v.is_empty() {
                return v;
            }
        }
        if let Ok(txt) = fs::read_to_string(self.config_path()) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt) {
                if let Some(s) = v.get("api_base").and_then(|x| x.as_str()) {
                    let s = s.trim().to_string();
                    if !s.is_empty() {
                        return s;
                    }
                }
            }
        }
        DEFAULT_API.to_string()
    }

    pub fn set_api_base(&self, base: &str) -> Result<(), String> {
        let base = base.trim().trim_end_matches('/');
        if base.is_empty() {
            return Err("地址不能为空".to_string());
        }
        if !(base.starts_with("http://") || base.starts_with("https://")) {
            return Err("地址要以 http:// 或 https:// 开头".to_string());
        }
        let mut obj = fs::read_to_string(self.config_path())
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .unwrap_or_else(|| serde_json::json!({}));
        obj["api_base"] = serde_json::Value::String(base.to_string());
        let txt = serde_json::to_string_pretty(&obj).map_err(|e| e.to_string())?;
        fs::write(self.config_path(), txt).map_err(|e| format!("保存配置失败:{}", e))?;
        Ok(())
    }
}
