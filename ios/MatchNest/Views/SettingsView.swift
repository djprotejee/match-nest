import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: MatchNestStore

    var body: some View {
        NavigationStack {
            ZStack {
                MatchNestTheme.background.ignoresSafeArea()
                List {
                    Section("Main") {
                        ForEach(store.entities.filter { $0.follow == .main }) { entity in
                            EntityRow(entity: entity)
                        }
                    }

                    Section("Starred") {
                        ForEach(store.entities.filter { $0.follow == .starred }) { entity in
                            EntityRow(entity: entity)
                        }
                    }

                    Section("Explore") {
                        ForEach(store.entities.filter { $0.follow == .explore }) { entity in
                            EntityRow(entity: entity)
                        }
                    }
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Settings")
            .task {
                if store.entities.isEmpty {
                    await store.load()
                }
            }
        }
    }
}

struct EntityRow: View {
    let entity: EntityItem

    var body: some View {
        HStack {
            Circle()
                .fill(Color(hex: entity.color))
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                Text(entity.name)
                    .foregroundStyle(MatchNestTheme.text)
                Text("\(entity.sport.title) · \(entity.follow.rawValue)")
                    .font(.caption)
                    .foregroundStyle(MatchNestTheme.secondary)
            }
            Spacer()
        }
        .listRowBackground(MatchNestTheme.surface)
    }
}

