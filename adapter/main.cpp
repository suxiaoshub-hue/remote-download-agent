#include <windows.h>
#include <bcrypt.h>
#include "status.h"
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

static constexpr wchar_t WindowClass[] = L"Global\\{4F7961BA-AD65-4018-BDEA-1BA1FF77CD66}";
static constexpr char ExpectedHash[] = "05b9927164f7b3a842f48ffc3bcfd464ae4052e2cdeec4f54902925f2178cdb6";

struct Handle {
    HANDLE value = nullptr;
    explicit Handle(HANDLE handle) : value(handle) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};

std::string Utf8(const std::wstring& text) {
    if (text.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
    return result;
}

std::wstring WideUtf8(const std::string& text) {
    if (text.empty()) return {};
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (!size) {
        size = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
        if (!size) return L"[输出编码错误]";
    }
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size);
    return result;
}

void ConsoleWriteUtf8(DWORD standardHandle, const std::string& message, bool flush = true) {
    HANDLE handle = GetStdHandle(standardHandle);
    DWORD mode = 0;
    if (handle && handle != INVALID_HANDLE_VALUE && GetConsoleMode(handle, &mode)) {
        const auto wide = WideUtf8(message);
        DWORD written = 0;
        WriteConsoleW(handle, wide.data(), static_cast<DWORD>(wide.size()), &written, nullptr);
    } else {
        std::ostream& stream = standardHandle == STD_ERROR_HANDLE ? std::cerr : std::cout;
        stream << message;
        if (flush) stream.flush();
    }
}

std::wstring ParentPath(const std::wstring& path) {
    const auto separator = path.find_last_of(L"\\/");
    if (separator == std::wstring::npos) return {};
    return path.substr(0, separator + 1);
}

std::wstring FileName(const std::wstring& path) {
    const auto separator = path.find_last_of(L"\\/");
    return separator == std::wstring::npos ? path : path.substr(separator + 1);
}

std::wstring JoinPath(const std::wstring& folder, const std::wstring& name) {
    if (folder.empty()) return name;
    if (folder.back() == L'\\' || folder.back() == L'/') return folder + name;
    return folder + L"\\" + name;
}

std::wstring AbsolutePath(const std::wstring& path) {
    DWORD required = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
    if (!required) throw std::runtime_error("无法解析文件路径：" + Utf8(path));
    std::vector<wchar_t> buffer(required);
    DWORD size = GetFullPathNameW(path.c_str(), required, buffer.data(), nullptr);
    if (!size || size >= required) throw std::runtime_error("无法解析文件路径：" + Utf8(path));
    return std::wstring(buffer.data(), size);
}

std::uintmax_t FileSize(const std::wstring& path) {
    WIN32_FILE_ATTRIBUTE_DATA attributes{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &attributes))
        throw std::runtime_error("读取日志文件大小失败，Windows 错误=" + std::to_string(GetLastError()));
    return (static_cast<std::uintmax_t>(attributes.nFileSizeHigh) << 32) | attributes.nFileSizeLow;
}

struct Reporter {
    std::ofstream file;
    explicit Reporter(const std::wstring& path) : file(path.c_str(), std::ios::binary | std::ios::trunc) {
        if (!file) throw std::runtime_error("无法写入结果文件：" + Utf8(path));
        file << "\xef\xbb\xbf";
    }
    void Write(const std::string& message) {
        file << message << "\r\n";
        file.flush();
        ConsoleWriteUtf8(STD_OUTPUT_HANDLE, message + "\r\n");
    }
};

std::wstring ExecutablePath(HANDLE process) {
    std::vector<wchar_t> buffer(32768);
    DWORD size = static_cast<DWORD>(buffer.size());
    if (!QueryFullProcessImageNameW(process, 0, buffer.data(), &size))
        throw std::runtime_error("读取进程路径失败，Windows 错误=" + std::to_string(GetLastError()));
    return std::wstring(buffer.data(), size);
}

std::wstring OwnFolder() {
    std::vector<wchar_t> buffer(32768);
    DWORD size = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!size || size >= buffer.size()) throw std::runtime_error("读取程序目录失败");
    return ParentPath(std::wstring(buffer.data(), size));
}

std::string Sha256(const std::wstring& path) {
    std::ifstream file(path.c_str(), std::ios::binary);
    if (!file) throw std::runtime_error("无法读取 PCStory 程序文件");
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        throw std::runtime_error("SHA256 初始化失败");
    std::array<UCHAR, 32> digest{};
    bool success = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    std::array<char, 65536> buffer{};
    while (success && file) {
        file.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        if (file.gcount()) success = BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(file.gcount()), 0) >= 0;
    }
    success = success && !file.bad();
    if (success) success = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (!success) throw std::runtime_error("SHA256 计算失败");
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    for (auto byte : digest) {
        result += digits[byte >> 4];
        result += digits[byte & 15];
    }
    return result;
}

struct WindowSearch {
    DWORD requestedPid = 0;
    std::vector<HWND> matches;
};

BOOL CALLBACK FindWindow(HWND window, LPARAM parameter) {
    auto& search = *reinterpret_cast<WindowSearch*>(parameter);
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (search.requestedPid && pid != search.requestedPid) return TRUE;
    wchar_t windowClass[256]{};
    if (GetClassNameW(window, windowClass, 256) && std::wstring(windowClass) == WindowClass)
        search.matches.push_back(window);
    return TRUE;
}

struct LogCursor { std::uintmax_t offset = 0; std::string partial; };

class LogTail {
    std::wstring folder;
    std::map<std::wstring, LogCursor> cursors;
    std::vector<std::wstring> Files() const {
        std::vector<std::wstring> files;
        DWORD attributes = GetFileAttributesW(folder.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY))
            throw std::runtime_error("无法读取日志目录：" + Utf8(folder));
        WIN32_FIND_DATAW entry{};
        HANDLE search = FindFirstFileW(JoinPath(folder, L"*").c_str(), &entry);
        if (search == INVALID_HANDLE_VALUE) {
            if (GetLastError() == ERROR_FILE_NOT_FOUND) return files;
            throw std::runtime_error("枚举日志目录失败，Windows 错误=" + std::to_string(GetLastError()));
        }
        DWORD error = ERROR_SUCCESS;
        try {
            do {
                const std::wstring name = entry.cFileName;
                if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) &&
                    name.rfind(L"pcstory_", 0) == 0 && name.size() >= 4 && name.compare(name.size() - 4, 4, L".log") == 0)
                    files.push_back(JoinPath(folder, name));
            } while (FindNextFileW(search, &entry));
            error = GetLastError();
        } catch (...) {
            FindClose(search);
            throw;
        }
        FindClose(search);
        if (error != ERROR_NO_MORE_FILES)
            throw std::runtime_error("枚举日志目录失败，Windows 错误=" + std::to_string(error));
        std::sort(files.begin(), files.end());
        return files;
    }
public:
    explicit LogTail(const std::wstring& logFolder) : folder(logFolder) {
        for (const auto& path : Files()) cursors[path].offset = FileSize(path);
    }
    std::vector<std::string> Read() {
        std::vector<std::string> lines;
        for (const auto& path : Files()) {
            auto& cursor = cursors[path];
            const auto size = FileSize(path);
            if (size < cursor.offset) { cursor.offset = 0; cursor.partial.clear(); }
            if (size == cursor.offset) continue;
            std::ifstream file(path.c_str(), std::ios::binary);
            if (!file) continue;
            file.seekg(static_cast<std::streamoff>(cursor.offset));
            std::string bytes(static_cast<std::size_t>(std::min<std::uintmax_t>(size - cursor.offset, 1048576)), '\0');
            file.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
            bytes.resize(static_cast<std::size_t>(file.gcount()));
            cursor.offset += bytes.size();
            cursor.partial += bytes;
            std::size_t start = 0, newline = 0;
            while ((newline = cursor.partial.find('\n', start)) != std::string::npos) {
                lines.push_back(cursor.partial.substr(start, newline - start));
                start = newline + 1;
            }
            cursor.partial.erase(0, start);
            if (cursor.partial.size() > 65536) cursor.partial.clear();
        }
        return lines;
    }
};

struct Options {
    DWORD gameId = 0;
    DWORD pid = 0;
    DWORD waitSeconds = 30;
    DWORD messageTimeout = 5000;
    std::wstring output;
    std::wstring logFolder;
};

std::wstring ReportPath(int argc, wchar_t** argv, const std::wstring& ownFolder) {
    for (int index = 1; index + 1 < argc; index += 2) {
        if (std::wstring(argv[index]) == L"--output") return AbsolutePath(argv[index + 1]);
    }
    return JoinPath(ownFolder, L"pcstory-download.txt");
}

std::string CurrentTimestamp() {
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char timestamp[32]{};
    std::snprintf(timestamp, sizeof(timestamp), "%04u-%02u-%02u %02u:%02u:%02u.%03u",
                  unsigned(time.wYear), unsigned(time.wMonth), unsigned(time.wDay),
                  unsigned(time.wHour), unsigned(time.wMinute), unsigned(time.wSecond), unsigned(time.wMilliseconds));
    return timestamp;
}

DWORD Number(const std::wstring& value, DWORD minimum, DWORD maximum) {
    if (value.empty() || value.find_first_not_of(L"0123456789") != std::wstring::npos)
        throw std::runtime_error("参数必须是十进制数字");
    auto number = std::stoull(value);
    if (number < minimum || number > maximum) throw std::runtime_error("数字参数超出允许范围");
    return static_cast<DWORD>(number);
}

Options Parse(int argc, wchar_t** argv, const std::wstring& ownFolder) {
    Options options;
    options.output = JoinPath(ownFolder, L"pcstory-download.txt");
    const auto config = JoinPath(ownFolder, L"pcstory-adapter.ini");
    wchar_t wait[32]{};
    GetPrivateProfileStringW(L"download", L"wait_seconds", L"30", wait, 32, config.c_str());
    options.waitSeconds = Number(wait, 0, 120);
    for (int index = 1; index < argc; ++index) {
        std::wstring key = argv[index];
        if (index + 1 >= argc) throw std::runtime_error("缺少参数值：" + Utf8(key));
        std::wstring value = argv[++index];
        if (key == L"--game-id") options.gameId = Number(value, 1, 2147483647);
        else if (key == L"--pid") options.pid = Number(value, 1, 4294967295UL);
        else if (key == L"--wait-seconds") options.waitSeconds = Number(value, 0, 120);
        else if (key == L"--message-timeout-ms") options.messageTimeout = Number(value, 100, 60000);
        else if (key == L"--output") options.output = AbsolutePath(value);
        else if (key == L"--log-dir") options.logFolder = AbsolutePath(value);
        else throw std::runtime_error("未知参数：" + Utf8(key));
    }
    if (!options.gameId) {
        ConsoleWriteUtf8(STD_OUTPUT_HANDLE, "请输入需要下载的游戏 GID（Roblox 为 5131）：");
        std::string input;
        std::getline(std::cin, input);
        options.gameId = Number(std::wstring(input.begin(), input.end()), 1, 2147483647);
    }
    if (options.gameId == 8049) throw std::runtime_error("此特殊游戏编号会触发 PCStory 强制更新，测试版不支持");
    return options;
}

int Download(const Options& options, Reporter& report) {
    WindowSearch search;
    search.requestedPid = options.pid;
    if (!EnumWindows(FindWindow, reinterpret_cast<LPARAM>(&search))) throw std::runtime_error("枚举窗口失败");
    if (search.matches.empty()) throw std::runtime_error("未找到 PCStory 主窗口，请先打开 PCStory");
    if (search.matches.size() != 1) throw std::runtime_error("找到多个 PCStory 主窗口，请使用 --pid 指定进程");
    HWND window = search.matches.front();
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_WRITE | SYNCHRONIZE, FALSE, pid));
    if (!process.value) throw std::runtime_error("打开 PCStory 失败，请使用相同管理员权限，Windows 错误=" + std::to_string(GetLastError()));
    const auto executable = ExecutablePath(process.value);
    report.Write("PCStory 路径：" + Utf8(executable));
    report.Write("PID=" + std::to_string(pid) + " GID=" + std::to_string(options.gameId));
#ifdef PCSTORY_TEST_FIXTURE
    if (FileName(executable) != L"PcstoryCommandFixture.exe") throw std::runtime_error("测试构建只能调用测试接收程序");
#else
    const auto hash = Sha256(executable);
    report.Write("SHA256=" + hash);
    if (hash != ExpectedHash) throw std::runtime_error("PCStory 版本不匹配，未发送命令；此版本只支持已分析的 6.4.2.0 文件");
#endif
    const auto logFolder = options.logFolder.empty() ? JoinPath(ParentPath(executable), L"log") : options.logFolder;
    LogTail logs(logFolder);
    report.Write("观察新增日志：" + Utf8(logFolder));
    struct Parameters {
        std::uint32_t reserved = 0;
        std::uint8_t force = 0;
        std::uint8_t extra = 0;
        std::uint16_t padding = 0;
        std::uint32_t option = 0;
    } parameters;
    static_assert(sizeof(Parameters) == 12 && offsetof(Parameters, force) == 4 && offsetof(Parameters, option) == 8);
    void* remote = VirtualAllocEx(process.value, nullptr, sizeof(parameters), MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!remote) throw std::runtime_error("分配下载参数失败，Windows 错误=" + std::to_string(GetLastError()));
    SIZE_T written = 0;
    if (!WriteProcessMemory(process.value, remote, &parameters, sizeof(parameters), &written) || written != sizeof(parameters)) {
        DWORD error = GetLastError();
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        throw std::runtime_error("写入下载参数失败，Windows 错误=" + std::to_string(error));
    }
    DWORD currentPid = 0;
    GetWindowThreadProcessId(window, &currentPid);
    if (currentPid != pid || WaitForSingleObject(process.value, 0) != WAIT_TIMEOUT) {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        throw std::runtime_error("PCStory 已关闭或窗口发生变化，未发送命令");
    }
    report.Write("发送新增下载消息 0x468，force=0，使用 PCStory 默认磁盘配置");
    const auto since = CurrentTimestamp();
    DWORD_PTR messageResult = 0;
    SetLastError(0);
    auto delivered = SendMessageTimeoutW(window, 0x468, options.gameId, reinterpret_cast<LPARAM>(remote),
                                        SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT,
                                        options.messageTimeout, &messageResult);
    if (!delivered) {
        report.Write("RESULT=UNCERTAIN 消息未确认或超时，Windows 错误=" + std::to_string(GetLastError()));
        report.Write("保留 12 字节参数，防止迟到消息访问已释放地址。请检查 PCStory 下载列表，不要自动重发。");
        return 5;
    }
    if (!VirtualFreeEx(process.value, remote, 0, MEM_RELEASE))
        report.Write("参数释放失败，Windows 错误=" + std::to_string(GetLastError()));
    report.Write("消息处理已返回，正在核对新日志（返回值不代表下载成功）");
    bool accepted = false;
    const auto deadline = GetTickCount64() + static_cast<ULONGLONG>(options.waitSeconds) * 1000;
    do {
        for (const auto& line : logs.Read()) {
            if (!IsCurrentLine(line, since)) continue;
            auto status = ClassifyLine(line, options.gameId);
            if (status == DownloadStatus::Pending) continue;
            report.Write("PCStory 新日志：" + line);
            if (status == DownloadStatus::Started) {
                report.Write("RESULT=STARTED PCStory 已记录此游戏开始下载，请核对下载列表和下载速度；这不代表下载完成。");
                return 0;
            }
            if (status == DownloadStatus::Failed) {
                report.Write("RESULT=FAILED PCStory 拒绝新增此游戏的下载任务。");
                return 3;
            }
            accepted = true;
        }
        if (WaitForSingleObject(process.value, 0) != WAIT_TIMEOUT)
            throw std::runtime_error("等待确认时 PCStory 已退出");
        if (GetTickCount64() >= deadline) break;
        Sleep(100);
    } while (true);
    if (accepted) {
        report.Write("RESULT=ACCEPTED 已新增任务，但未观察到开始下载；请检查等待队列和 PCStory 配置。");
        return 2;
    }
    report.Write("RESULT=UNCONFIRMED 消息已返回，但没有此 GID 的新增下载日志；请检查 PCStory 下载列表。");
    return 4;
}

int wmain(int argc, wchar_t** argv) {
    SetConsoleOutputCP(CP_UTF8);
    try {
        auto ownFolder = OwnFolder();
        Reporter report(ReportPath(argc, argv, ownFolder));
        report.Write("PCStory 直接下载测试 v0.1.1（兼容旧版 Windows 文件接口）");
        try {
            auto options = Parse(argc, argv, ownFolder);
            return Download(options, report);
        } catch (const std::exception& error) {
            report.Write(std::string("RESULT=ERROR ") + error.what());
            return 1;
        }
    } catch (const std::exception& error) {
        ConsoleWriteUtf8(STD_ERROR_HANDLE, std::string(error.what()) + "\r\n");
        return 1;
    }
}
