#include "status.h"

DownloadStatus ClassifyLine(const std::string& line, std::uint32_t gameId) {
    const auto number = std::to_string(gameId);
    if (line.find("[" + number + ":") != std::string::npos &&
        line.find("] start download") != std::string::npos) {
        return DownloadStatus::Started;
    }
    if (line.find("gid=" + number + ",") != std::string::npos) {
        if (line.find("donwdlg add task fail,") != std::string::npos)
            return DownloadStatus::Failed;
        if (line.find("donwdlg add task ok,") != std::string::npos)
            return DownloadStatus::Accepted;
    }
    return DownloadStatus::Pending;
}
