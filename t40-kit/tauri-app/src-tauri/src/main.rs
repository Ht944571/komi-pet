// T4.0 选型验证探针（Tauri v2）：验证 macOS 上透明窗口 / 点击穿透 / DPI 三项。
// 三项全达标 → Tauri v2 定案；任一不达标 → 退回 Electron（任务文档 §6 T4.0）。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

#[tauri::command]
fn set_click_through(window: tauri::Window, enabled: bool) -> Result<(), String> {
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn dpi_info(window: tauri::Window) -> serde_json::Value {
    let scale = window.scale_factor().unwrap_or(0.0);
    let size = window.inner_size().unwrap_or_default();
    serde_json::json!({
        "scale_factor": scale,
        "logical": { "w": size.width / scale as f64, "h": size.height / scale as f64 },
        "physical": { "w": size.width, "h": size.height }
    })
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![set_click_through, dpi_info])
        .setup(|app| {
            let win = app
                .get_webview_window("main")
                .expect("main window missing");
            // 探针初始为「点击穿透」，界面上按空格切换（穿透时鼠标点不到自己，键盘仍可用）
            let _ = win.set_ignore_cursor_events(true);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
