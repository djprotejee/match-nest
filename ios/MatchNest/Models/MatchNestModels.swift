import Foundation
import SwiftUI

enum Sport: String, Codable, CaseIterable, Identifiable {
    case formula
    case cs2
    case football

    var id: String { rawValue }

    var title: String {
        switch self {
        case .formula: return "Formula"
        case .cs2: return "CS2"
        case .football: return "Football"
        }
    }

    var color: Color {
        switch self {
        case .formula: return Color(hex: "#F04438")
        case .cs2: return Color(hex: "#F4B740")
        case .football: return Color(hex: "#2ECC71")
        }
    }
}

enum EventStatus: String, Codable, CaseIterable, Identifiable {
    case past
    case live
    case upcoming
    case tbd

    var id: String { rawValue }
}

enum FollowLevel: String, Codable, CaseIterable, Identifiable {
    case main
    case starred
    case muted
    case hidden
    case explore

    var id: String { rawValue }
}

struct MatchEvent: Identifiable, Codable, Equatable {
    let id: String
    let title: String
    let sport: Sport
    let startsAt: Date?
    let status: EventStatus
    let entityIds: [String]
    let source: String
    let competition: String?
    let sessionType: String?
    let resultSummary: String?
    let importance: Int
    let followLevel: FollowLevel
    let resultHidden: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case sport
        case startsAt = "starts_at"
        case status
        case entityIds = "entity_ids"
        case source
        case competition
        case sessionType = "session_type"
        case resultSummary = "result_summary"
        case importance
        case followLevel = "follow_level"
        case resultHidden = "result_hidden"
    }
}

struct DayGroup: Identifiable, Codable {
    let date: String
    let events: [MatchEvent]

    var id: String { date }
}

struct EntityItem: Identifiable, Codable {
    let id: String
    let name: String
    let sport: Sport
    let kind: String
    let color: String
    let follow: FollowLevel
}

extension Color {
    init(hex: String) {
        let trimmed = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: trimmed).scanHexInt64(&int)
        let red = Double((int >> 16) & 0xFF) / 255.0
        let green = Double((int >> 8) & 0xFF) / 255.0
        let blue = Double(int & 0xFF) / 255.0
        self.init(red: red, green: green, blue: blue)
    }
}

