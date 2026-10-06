#include "status.h"

bool IsCurrentLine(const std::string& line, const std::string& since) {
    if (line.size() < 25 || line[0] != '[' || line[24] != ']' || since.size() != 23)
        return false;
    const auto timestamp = line.substr(1, 23);
    const std::string format = "0000-00-00 00:00:00.000";
    for (std::size_t position = 0; position < format.size(); ++position) {
        if (format[position] == '0') {
            if (timestamp[position] < '0' || timestamp[position] > '9') return false;
        } else if (timestamp[position] != format[position]) return false;
    }
    return timestamp >= since;
}

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
