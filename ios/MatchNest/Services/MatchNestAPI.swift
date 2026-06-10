import Foundation

@MainActor
final class MatchNestStore: ObservableObject {
    @Published var timeline: [DayGroup] = []
    @Published var entities: [EntityItem] = []
    @Published var selectedRange: String = "week"
    @Published var revealSpoilers = false
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let api = MatchNestAPI()

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let timelineResponse = api.timeline(range: selectedRange, revealSpoilers: revealSpoilers)
            async let entitiesResponse = api.entities()
            timeline = try await timelineResponse
            entities = try await entitiesResponse
            writeNextEventForWidget()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func writeNextEventForWidget() {
        // WidgetKit extensions cannot freely run the same network flow as the
        // app. The app writes a compact next-event payload into the shared app
        // group store, and the widget reads that snapshot on its timeline.
        let event = timeline.flatMap(\.events).first { $0.status == .live || $0.status == .upcoming }
        guard let event else { return }
        let widgetEvent = WidgetEventPayload(
            title: event.title,
            sport: event.sport.rawValue,
            startsAt: event.startsAt,
            status: event.status.rawValue,
            competition: event.competition
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(widgetEvent),
              let defaults = UserDefaults(suiteName: "group.matchnest")
        else {
            return
        }
        defaults.set(data, forKey: "next_event")
    }
}

private struct WidgetEventPayload: Encodable {
    let title: String
    let sport: String
    let startsAt: Date?
    let status: String
    let competition: String?

    enum CodingKeys: String, CodingKey {
        case title
        case sport
        case startsAt = "startsAt"
        case status
        case competition
    }
}

struct MatchNestAPI {
    var baseURL = URL(string: "http://127.0.0.1:8000")!

    func timeline(range: String, revealSpoilers: Bool) async throws -> [DayGroup] {
        var components = URLComponents(url: baseURL.appendingPathComponent("timeline"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "range", value: range),
            URLQueryItem(name: "reveal_spoilers", value: revealSpoilers ? "true" : "false")
        ]
        return try await get(components.url!)
    }

    func calendar(year: Int, month: Int, revealSpoilers: Bool) async throws -> [DayGroup] {
        var components = URLComponents(url: baseURL.appendingPathComponent("calendar/\(year)/\(month)"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "reveal_spoilers", value: revealSpoilers ? "true" : "false")]
        return try await get(components.url!)
    }

    func entities() async throws -> [EntityItem] {
        try await get(baseURL.appendingPathComponent("entities"))
    }

    private func get<T: Decodable>(_ url: URL) async throws -> T {
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse, 200..<300 ~= httpResponse.statusCode else {
            throw URLError(.badServerResponse)
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(T.self, from: data)
    }
}
