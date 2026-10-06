#pragma once
#include <cstdint>
#include <string>

enum class DownloadStatus { Pending, Accepted, Started, Failed };
DownloadStatus ClassifyLine(const std::string& line, std::uint32_t gameId);
