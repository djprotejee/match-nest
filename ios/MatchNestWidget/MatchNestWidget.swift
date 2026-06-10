import WidgetKit
import SwiftUI

struct NextEventEntry: TimelineEntry {
    let date: Date
    let event: WidgetEvent?
}

struct WidgetEvent: Codable {
    let title: String
    let sport: String
    let startsAt: Date?
    let status: String
    let competition: String?
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> NextEventEntry {
        NextEventEntry(
            date: Date(),
            event: WidgetEvent(
                title: "NAVI vs Vitality",
                sport: "CS2",
                startsAt: Date().addingTimeInterval(3600),
                status: "upcoming",
                competition: "BLAST"
            )
        )
    }

    func getSnapshot(in context: Context, completion: @escaping (NextEventEntry) -> Void) {
        completion(placeholder(in: context))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NextEventEntry>) -> Void) {
        let event = loadSharedEvent()
        let entry = NextEventEntry(date: Date(), event: event)
        let nextRefresh = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date().addingTimeInterval(900)
        completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
    }

    private func loadSharedEvent() -> WidgetEvent? {
        // The widget reads the last synced event from the app group. If the app
        // has not synced yet, the UI falls back to an empty state.
        guard let defaults = UserDefaults(suiteName: "group.matchnest"),
              let data = defaults.data(forKey: "next_event")
        else {
            return nil
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(WidgetEvent.self, from: data)
    }
}

struct MatchNestWidgetEntryView: View {
    var entry: Provider.Entry

    var body: some View {
        ZStack {
            Color(hex: "#15171D")
            if let event = entry.event {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(event.sport.uppercased())
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(accent(for: event.sport))
                        Spacer()
                        Text(event.status.uppercased())
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Color(hex: "#FF8A3D"))
                    }

                    Text(event.title)
                        .font(.headline.weight(.bold))
                        .foregroundStyle(Color(hex: "#E8EAF0"))
                        .lineLimit(2)

                    if let startsAt = event.startsAt {
                        Text(startsAt.formatted(date: .abbreviated, time: .shortened))
                            .font(.caption)
                            .foregroundStyle(Color(hex: "#9AA3B2"))
                    } else {
                        Text("Time TBD")
                            .font(.caption)
                            .foregroundStyle(Color(hex: "#9AA3B2"))
                    }

                    if let competition = event.competition {
                        Text(competition)
                            .font(.caption2)
                            .foregroundStyle(Color(hex: "#9AA3B2"))
                    }
                }
                .padding()
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text("MatchNest")
                        .font(.headline.weight(.bold))
                        .foregroundStyle(Color(hex: "#E8EAF0"))
                    Text("No followed events")
                        .font(.caption)
                        .foregroundStyle(Color(hex: "#9AA3B2"))
                }
                .padding()
            }
        }
    }

    private func accent(for sport: String) -> Color {
        switch sport.lowercased() {
        case "formula": return Color(hex: "#F04438")
        case "cs2": return Color(hex: "#F4B740")
        case "football": return Color(hex: "#2ECC71")
        default: return Color(hex: "#FF8A3D")
        }
    }
}

@main
struct MatchNestWidget: Widget {
    let kind: String = "MatchNestWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: Provider()) { entry in
            MatchNestWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("MatchNest")
        .description("Shows the next followed event.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
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
