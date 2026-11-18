// Core /api/* 的极薄 HTTP 客户端。
// 只做四件事:带 token、把 HTTP 错误翻成中文、超时、JSON 进出。
use std::time::Duration;

use serde_json::Value;

#[derive(Debug)]
pub struct ApiError {
    pub message: String,
    pub status: Option<u16>,
}

impl ApiError {
    fn new(message: impl Into<String>, status: Option<u16>) -> Self {
        Self { message: message.into(), status }
    }
}

pub struct Client {
    base: String,
    token: Option<String>,
}

impl Client {
    pub fn new(base: &str, token: Option<&str>) -> Self {
        Self {
            base: base.trim().trim_end_matches('/').to_string(),
            token: token.map(|s| s.to_string()),
        }
    }

    fn build(&self, method: &str, path: &str) -> ureq::Request {
        let url = format!("{}{}", self.base, path);
        let mut req = ureq::request(method, &url)
            .timeout(Duration::from_secs(20))
            .set("Accept", "application/json")
            .set("User-Agent", "RivuletRuo/0.1");
        if let Some(t) = self.token.as_deref() {
            req = req.set("Authorization", &format!("Bearer {}", t));
        }
        req
    }

    pub fn get(&self, path: &str) -> Result<Value, ApiError> {
        let res = self.build("GET", path).call();
        finish(res)
    }

    pub fn post(&self, path: &str, body: &Value) -> Result<Value, ApiError> {
        let req = self.build("POST", path).set("Content-Type", "application/json");
        let res = req.send_json(body.clone());
        finish(res)
    }

    pub fn delete(&self, path: &str) -> Result<Value, ApiError> {
        let res = self.build("DELETE", path).call();
        finish(res)
    }

    pub fn patch(&self, path: &str, body: &Value) -> Result<Value, ApiError> {
        let req = self.build("PATCH", path).set("Content-Type", "application/json");
        let res = req.send_json(body.clone());
        finish(res)
    }
}

fn finish(res: Result<ureq::Response, ureq::Error>) -> Result<Value, ApiError> {
    match res {
        Ok(resp) => body_json(resp),
        Err(ureq::Error::Status(code, resp)) => {
            let detail = body_json(resp)
                .ok()
                .and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(|s| s.to_string()));
            Err(ApiError::new(detail.unwrap_or_else(|| status_text(code)), Some(code)))
        }
        Err(ureq::Error::Transport(t)) => Err(ApiError::new(transport_text(&t), None)),
    }
}

fn body_json(resp: ureq::Response) -> Result<Value, ApiError> {
    let status = resp.status();
    let text = resp.into_string().map_err(|e| ApiError::new(format!("读服务器回包失败:{}", e), Some(status)))?;
    if text.trim().is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(&text).map_err(|_| ApiError::new("服务器回的不是 JSON", Some(status)))
}

fn status_text(code: u16) -> String {
    match code {
        400 => "请求不被接受".to_string(),
        401 => "登录已过期,请重新登录".to_string(),
        403 => "没有权限".to_string(),
        404 => "接口不存在".to_string(),
        409 => "已经存在了".to_string(),
        429 => "操作太频繁,缓一缓".to_string(),
        500..=599 => format!("服务器出错了({})", code),
        _ => format!("服务器返回 {}", code),
    }
}

fn transport_text(t: &ureq::Transport) -> String {
    match t.kind() {
        ureq::ErrorKind::Dns | ureq::ErrorKind::ConnectionFailed | ureq::ErrorKind::Io => {
            "连不上账号服务,检查网络或服务器地址".to_string()
        }
        ureq::ErrorKind::InvalidUrl | ureq::ErrorKind::UnknownScheme | ureq::ErrorKind::InvalidProxyUrl => {
            format!("服务器地址不对:{}", t)
        }
        _ => format!("网络出错:{}", t),
    }
}
