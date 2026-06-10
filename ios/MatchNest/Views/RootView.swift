import SwiftUI

struct RootView: View {
    var body: some View {
        TabView {
            TimelineView()
                .tabItem {
                    Label("Timeline", systemImage: "list.bullet.rectangle")
                }

            CalendarScreen()
                .tabItem {
                    Label("Calendar", systemImage: "calendar")
                }

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "slider.horizontal.3")
                }
        }
        .tint(MatchNestTheme.orange)
        .preferredColorScheme(.dark)
    }
}

enum MatchNestTheme {
    static let background = Color(hex: "#15171D")
    static let surface = Color(hex: "#1B1E26")
    static let surfaceRaised = Color(hex: "#222633")
    static let border = Color(hex: "#303545")
    static let text = Color(hex: "#E8EAF0")
    static let secondary = Color(hex: "#9AA3B2")
    static let orange = Color(hex: "#FF8A3D")
    static let violet = Color(hex: "#8B5CF6")
}

