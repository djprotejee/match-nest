import SwiftUI

@main
struct MatchNestApp: App {
    @StateObject private var store = MatchNestStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .task {
                    await store.load()
                }
        }
    }
}

