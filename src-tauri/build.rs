fn main() {
    println!("cargo:rustc-check-cfg=cfg(rivulet_embedded_bundle)");
    println!("cargo:rerun-if-changed=assets/bundle.zip");
    if std::path::Path::new("assets/bundle.zip").is_file() {
        println!("cargo:rustc-cfg=rivulet_embedded_bundle");
    }
    tauri_build::build()
}
