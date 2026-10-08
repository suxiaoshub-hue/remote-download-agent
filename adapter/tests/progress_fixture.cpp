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

INT_PTR CALLBACK WindowProc(HWND window, UINT message, WPARAM first, LPARAM second) {
    if (message == WM_COMMAND) {
        int selected = static_cast<int>(SendMessageW(downloadList, LVM_GETNEXTITEM, -1, LVNI_SELECTED));
        if (selected < 0 || SendMessageW(downloadList, LVM_GETNEXTITEM, selected, LVNI_SELECTED) != -1) return FALSE;
        wchar_t identity[32]{};
        LVITEMW item{};
        item.iSubItem = 1;
        item.pszText = identity;
        item.cchTextMax = 32;
        SendMessageW(downloadList, LVM_GETITEMTEXTW, selected, reinterpret_cast<LPARAM>(&item));
        if (std::wstring(identity) != L"5131") return FALSE;
        if (first == 0x8016) SetCell(selected, 3, L"暂停下载");
        else if (first == 0x8017) SetCell(selected, 3, L"正在下载");
        else if (first == 0x8018) SendMessageW(downloadList, LVM_DELETEITEM, selected, 0);
        else return FALSE;
        return TRUE;
    }
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
    return FALSE;
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) return 1;
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_LISTVIEW_CLASSES};
    if (!InitCommonControlsEx(&controls)) return 2;
    alignas(DWORD) unsigned char templateBytes[64]{};
    auto dialog = reinterpret_cast<DLGTEMPLATE*>(templateBytes);
    dialog->style = WS_OVERLAPPEDWINDOW;
    dialog->cx = 900;
    dialog->cy = 200;
    HINSTANCE instance = GetModuleHandleW(nullptr);
    HWND window = CreateDialogIndirectParamW(instance, dialog, nullptr, WindowProc, 0);
    if (!window) return 4;
    CreateWindowW(WC_LISTVIEWW, L"unrelated", WS_CHILD | LVS_REPORT, 0, 0, 50, 20, window, nullptr, instance, nullptr);
    downloadList = CreateWindowW(WC_LISTVIEWW, L"download", WS_CHILD | LVS_REPORT, 0, 0, 900, 100, window, reinterpret_cast<HMENU>(1003), instance, nullptr);
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
    SetCell(0, 2, L"12.00%");
    SetCell(0, 3, L"正在下载");
    SetCell(1, 1, L"5131");
    SetCell(1, 2, L"2.97%");
    SetCell(1, 3, L"正在下载");
    SetCell(1, 4, L"1024.00");
    SetCell(1, 5, L"604.69");
    SetCell(1, 6, L"623.20");
    LVITEMW selected{};
    selected.stateMask = LVIS_SELECTED;
    selected.state = LVIS_SELECTED;
    SendMessageW(downloadList, LVM_SETITEMSTATE, 0, reinterpret_cast<LPARAM>(&selected));
    std::ofstream(argv[1]) << reinterpret_cast<std::uintptr_t>(window);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
