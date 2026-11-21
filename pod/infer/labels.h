/*
 * labels.h -- loads models/labels.csv (agent-2's label table). Owner: agent-2 (P1-2).
 * Header-only C++17, no TFLite dependency.
 *
 * Maps a raw model class index to a Pod class_id. Indices not in the table map to
 * kDrop, so anything we do not list -- person in particular -- never leaves pod-infer.
 */
#ifndef POD_INFER_LABELS_H
#define POD_INFER_LABELS_H

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace pod {

constexpr int kDrop = -1;

struct LabelTable {
    std::vector<int> by_model_index;      /* model index -> class_id, or kDrop */
    std::vector<std::string> species;     /* class_id -> species name */

    int class_for(int model_index) const {
        if (model_index < 0 || model_index >= static_cast<int>(by_model_index.size())) return kDrop;
        return by_model_index[static_cast<size_t>(model_index)];
    }
    const char* name(int class_id) const {
        if (class_id < 0 || class_id >= static_cast<int>(species.size()) || species[class_id].empty())
            return "unknown";
        return species[static_cast<size_t>(class_id)].c_str();
    }
};

/* Returns false (with a message in *err) on a missing file or a bad row.
 * A "person" row is rejected outright: person must never be a Pod class. */
inline bool load_labels(const std::string& path, LabelTable* t, std::string* err) {
    std::ifstream f(path);
    if (!f) { *err = "cannot open " + path; return false; }
    t->by_model_index.assign(256, kDrop);
    t->species.clear();
    std::string line;
    int lineno = 0;
    bool header_seen = false;
    while (std::getline(f, line)) {
        ++lineno;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        if (!header_seen) { header_seen = true; continue; }   /* class_id,species,model_index,demo_only */
        std::stringstream ss(line);
        std::string id_s, name, idx_s;
        std::getline(ss, id_s, ',');
        std::getline(ss, name, ',');
        std::getline(ss, idx_s, ',');
        int id = -1;
        try { id = std::stoi(id_s); } catch (...) { id = -1; }
        if (id < 0 || id > 65535 || name.empty()) {
            *err = path + ":" + std::to_string(lineno) + ": bad row";
            return false;
        }
        if (name == "person") {
            *err = path + ":" + std::to_string(lineno) + ": person is not allowed in the label table";
            return false;
        }
        if (static_cast<size_t>(id) >= t->species.size()) t->species.resize(static_cast<size_t>(id) + 1);
        t->species[static_cast<size_t>(id)] = name;
        if (!idx_s.empty()) {
            int idx = -1;
            try { idx = std::stoi(idx_s); } catch (...) { idx = -1; }
            if (idx < 0 || idx >= 256) {
                *err = path + ":" + std::to_string(lineno) + ": bad model_index";
                return false;
            }
            t->by_model_index[static_cast<size_t>(idx)] = id;
        }
    }
    if (t->species.empty()) { *err = path + ": no rows"; return false; }
    return true;
}

}  // namespace pod

#endif /* POD_INFER_LABELS_H */
