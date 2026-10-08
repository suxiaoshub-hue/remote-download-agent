#include <windows.h>
#include <commctrl.h>
#include <cstdint>
#include <fstream>
#include <string>

static HWND downloadList = nullptr;

void SetCell(int row, int column, const wchar_t* value) {
    LVITEMW item{};
    item.iSubItem = column;
    item.pszText = const_cast<wchar_t*>(value);
    SendMessageW(downloadList, LVM_SETITEMTEXTW, row, reinterpret_cast<LPARAM>(&item));
}

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM first, LPARAM second) {
    if (message == WM_APP + 1) {
        SetCell(1, 2, L"17.25%");
        SetCell(1, 3, L"暂停下载");
        return 1;
    }
    if (message == WM_APP + 2) {
        SetCell(1, 2, L"");
        SetCell(1, 3, L"校验中");
        return 1;
    }
    if (message == WM_CLOSE) { DestroyWindow(window); return 0; }
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, first, second);
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) return 1;
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_LISTVIEW_CLASSES};
    if (!InitCommonControlsEx(&controls)) return 2;
    WNDCLASSW definition{};
    definition.lpfnWndProc = WindowProc;
    definition.hInstance = GetModuleHandleW(nullptr);
    definition.lpszClassName = L"PcstoryProgressFixture";
    if (!RegisterClassW(&definition)) return 3;
    HWND window = CreateWindowW(definition.lpszClassName, L"Progress Fixture", WS_OVERLAPPEDWINDOW,
                                0, 0, 900, 200, nullptr, nullptr, definition.hInstance, nullptr);
    if (!window) return 4;
    CreateWindowW(WC_LISTVIEWW, L"unrelated", WS_CHILD | LVS_REPORT, 0, 0, 50, 20, window, nullptr, definition.hInstance, nullptr);
    downloadList = CreateWindowW(WC_LISTVIEWW, L"download", WS_CHILD | LVS_REPORT, 0, 0, 900, 100, window, nullptr, definition.hInstance, nullptr);
    if (!downloadList) return 5;
    const wchar_t* headers[] = {L"游戏名", L"ID", L"进度", L"状态", L"速度(KB/S)", L"剩余(MB)", L"更新量(MB)"};
    for (int column = 0; column < 7; ++column) {
        LVCOLUMNW value{};
        value.mask = LVCF_TEXT | LVCF_WIDTH;
        value.pszText = const_cast<wchar_t*>(headers[column]);
        value.cx = 100;
        SendMessageW(downloadList, LVM_INSERTCOLUMNW, column, reinterpret_cast<LPARAM>(&value));
    }
    for (int row = 0; row < 2; ++row) {
        LVITEMW item{};
        item.mask = LVIF_TEXT;
        item.iItem = row;
        item.pszText = const_cast<wchar_t*>(row ? L"Roblox" : L"另一游戏");
        SendMessageW(downloadList, LVM_INSERTITEMW, 0, reinterpret_cast<LPARAM>(&item));
    }
    SetCell(0, 1, L"9999");
    SetCell(1, 1, L"5131");
    SetCell(1, 2, L"2.97%");
    SetCell(1, 3, L"正在下载");
    SetCell(1, 4, L"1024.00");
    SetCell(1, 5, L"604.69");
    SetCell(1, 6, L"623.20");
    std::ofstream(argv[1]) << reinterpret_cast<std::uintptr_t>(window);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
