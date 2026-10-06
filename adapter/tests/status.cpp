#include "../status.h"
#include <iostream>

int main() {
    struct Case { const char* line; DownloadStatus expected; };
    const Case cases[] = {
        {"[info] donwdlg recv add task req,gid=5131,force=0", DownloadStatus::Pending},
        {"[info] donwdlg add task ok,gid=5131,force=false", DownloadStatus::Accepted},
        {"[error] donwdlg add task fail,gid=5131,force=false", DownloadStatus::Failed},
        {"[info] [5131:Roblox] start download", DownloadStatus::Started},
        {"[info] [51310:Other] start download", DownloadStatus::Pending},
        {"[info] donwdlg add task ok,gid=51310,force=false", DownloadStatus::Pending},
        {"[error] Get version failed", DownloadStatus::Pending},
        {"[info] [5131:Roblox] start tark ok,forceupdate=0", DownloadStatus::Pending}
    };
    for (const auto& testCase : cases) {
        if (ClassifyLine(testCase.line, 5131) != testCase.expected) {
            std::cerr << "Unexpected status: " << testCase.line << '\n';
            return 1;
        }
    }
    std::cout << "All 8 status cases passed\n";
}
