import SwiftUI

struct TimelineView: View {
    @EnvironmentObject private var store: MatchNestStore

    var body: some View {
        NavigationStack {
            ZStack {
                MatchNestTheme.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        header
                        rangePicker
                        spoilerToggle
                        statusStrip
                        dayGroups
                    }
                    .padding(16)
                }
            }
            .navigationBarHidden(true)
            .refreshable {
                await store.load()
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("MatchNest")
                .font(.system(size: 32, weight: .bold))
                .foregroundStyle(MatchNestTheme.text)
            Text("Main feed for F1, NAVI CS2, Ukraine and Barcelona")
                .font(.subheadline)
                .foregroundStyle(MatchNestTheme.secondary)
        }
    }

    private var rangePicker: some View {
        Picker("Range", selection: $store.selectedRange) {
            Text("Today").tag("today")
            Text("Week").tag("week")
            Text("Month").tag("month")
        }
        .pickerStyle(.segmented)
        .onChange(of: store.selectedRange) {
            Task { await store.load() }
        }
    }

    private var spoilerToggle: some View {
        Toggle(isOn: $store.revealSpoilers) {
            Label(store.revealSpoilers ? "Scores visible" : "Hide spoilers", systemImage: store.revealSpoilers ? "eye" : "eye.slash")
                .foregroundStyle(MatchNestTheme.text)
        }
        .tint(MatchNestTheme.orange)
        .onChange(of: store.revealSpoilers) {
            Task { await store.load() }
        }
    }

    private var statusStrip: some View {
        HStack(spacing: 8) {
            StatusPill(title: "Past", color: MatchNestTheme.secondary)
            StatusPill(title: "Live", color: Color(hex: "#FF3B30"))
            StatusPill(title: "Upcoming", color: MatchNestTheme.orange)
        }
    }

    private var dayGroups: some View {
        VStack(alignment: .leading, spacing: 18) {
            if store.isLoading && store.timeline.isEmpty {
                ProgressView()
                    .tint(MatchNestTheme.orange)
                    .frame(maxWidth: .infinity, minHeight: 120)
            } else if store.timeline.isEmpty {
                EmptyStateView(title: "No events", subtitle: "Try another range or enable more follows.")
            } else {
                ForEach(store.timeline) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(group.date)
                            .font(.headline)
                            .foregroundStyle(MatchNestTheme.secondary)
                        ForEach(group.events) { event in
                            EventCard(event: event)
                        }
                    }
                }
            }
        }
    }
}

struct StatusPill: View {
    let title: String
    let color: Color

    var body: some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(MatchNestTheme.text)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(color.opacity(0.16), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.36), lineWidth: 1))
    }
}

struct EmptyStateView: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 8) {
            Text(title)
                .font(.headline)
                .foregroundStyle(MatchNestTheme.text)
            Text(subtitle)
                .font(.subheadline)
                .foregroundStyle(MatchNestTheme.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 160)
        .background(MatchNestTheme.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(MatchNestTheme.border, lineWidth: 1))
    }
}

