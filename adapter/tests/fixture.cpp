#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>

namespace fs = std::filesystem;
static fs::path logPath;
static fs::path recordPath;
static std::wstring mode;
static constexpr wchar_t WindowClass[] = L"Global\\{4F7961BA-AD65-4018-BDEA-1BA1FF77CD66}";

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM gameId, LPARAM parameters) {
    if (message == 0x468) {
        if (mode == L"timeout") Sleep(1000);
        auto buffer = reinterpret_cast<const std::uint8_t*>(parameters);
        std::uint32_t option = 0;
        memcpy(&option, buffer + 8, sizeof(option));
        std::ofstream record(recordPath);
        record << gameId << ' ' << unsigned(buffer[4]) << ' ' << unsigned(buffer[5])
               << ' ' << option << '\n';
        record.close();
        if (mode == L"rotation") {
            fs::rename(logPath, logPath.parent_path() / L"pcstory_archive.log");
            return 0;
        }
        SYSTEMTIME time{};
        GetLocalTime(&time);
        char timestamp[32]{};
        std::snprintf(timestamp, sizeof(timestamp), "[%04u-%02u-%02u %02u:%02u:%02u.%03u] ",
                      unsigned(time.wYear), unsigned(time.wMonth), unsigned(time.wDay),
                      unsigned(time.wHour), unsigned(time.wMinute), unsigned(time.wSecond), unsigned(time.wMilliseconds));
        std::ofstream log(logPath, std::ios::app);
        if (mode == L"failed") {
            log << timestamp << "donwdlg add task fail,gid=" << gameId << ",force=false\n";
        } else if (mode == L"accepted") {
            log << timestamp << "donwdlg add task ok,gid=" << gameId << ",force=false\n";
        } else if (mode == L"started") {
            log << timestamp << "donwdlg add task ok,gid=" << gameId << ",force=false\n";
            log << timestamp << '[' << gameId << ":Dota2\xB9\xFA\xBC\xCA\xB7\xFE]:: start download\n";
            log << timestamp << '[' << gameId << ":Fixture] start download\n";
        } else if (mode == L"wrong-gid") {
            log << timestamp << '[' << gameId << "0:Fixture] start download\n";
        }
        log.flush();
        return 0;
    }
    if (message == WM_CLOSE) { DestroyWindow(window); return 0; }
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, gameId, parameters);
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 3) return 1;
    fs::path folder(argv[1]);
    mode = argv[2];
    fs::create_directories(folder / L"log");
    logPath = folder / L"log" / L"pcstory_fixture.log";
    recordPath = folder / L"parameters.txt";
    std::ofstream(logPath) << "[2000-01-01 00:00:00.000] [5131:Old] start download\n";
    WNDCLASSW windowClass{};
    windowClass.lpfnWndProc = WindowProc;
    windowClass.hInstance = GetModuleHandleW(nullptr);
    windowClass.lpszClassName = WindowClass;
    if (!RegisterClassW(&windowClass)) return 2;
    HWND window = CreateWindowW(WindowClass, L"PCStory Command Fixture", WS_OVERLAPPEDWINDOW,
                               0, 0, 100, 100, nullptr, nullptr, windowClass.hInstance, nullptr);
    if (!window) return 3;
    std::ofstream(folder / L"ready.txt") << GetCurrentProcessId();
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
