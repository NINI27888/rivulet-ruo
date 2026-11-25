use sha2::{Digest, Sha256};
use std::fs::File;
use std::io;

fn main() {
    println!("cargo:rustc-check-cfg=cfg(rivulet_embedded_bundle)");
    println!("cargo:rerun-if-changed=assets/bundle.zip");
    if let Ok(mut bundle) = File::open("assets/bundle.zip") {
        let mut digest = Sha256::new();
        io::copy(&mut bundle, &mut digest).expect("read embedded runtime");
        println!("cargo:rustc-env=RIVULET_BUNDLE_SHA={:x}", digest.finalize());
        println!("cargo:rustc-cfg=rivulet_embedded_bundle");
    }
    tauri_build::build()
}
