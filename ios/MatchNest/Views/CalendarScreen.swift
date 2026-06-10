import SwiftUI

struct CalendarScreen: View {
    @EnvironmentObject private var store: MatchNestStore
    @State private var monthDate = Date()
    @State private var groups: [DayGroup] = []
    @State private var isLoading = false

    private let calendar = Calendar.current
    private let api = MatchNestAPI()

    var body: some View {
        NavigationStack {
            ZStack {
                MatchNestTheme.background.ignoresSafeArea()
                ScrollView {
                    VStack(spacing: 16) {
                        header
                        monthGrid
                        eventList
                    }
                    .padding(16)
                }
            }
            .navigationTitle("Calendar")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadMonth() }
        }
    }

    private var header: some View {
        HStack {
            Button {
                shiftMonth(-1)
            } label: {
                Image(systemName: "chevron.left")
            }
            .buttonStyle(.bordered)

            Spacer()

            Text(monthDate.formatted(.dateTime.month(.wide).year()))
                .font(.title3.weight(.bold))
                .foregroundStyle(MatchNestTheme.text)

            Spacer()

            Button {
                shiftMonth(1)
            } label: {
                Image(systemName: "chevron.right")
            }
            .buttonStyle(.bordered)
        }
        .tint(MatchNestTheme.orange)
    }

    private var monthGrid: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 7), spacing: 8) {
            ForEach(weekdaySymbols, id: \.self) { symbol in
                Text(symbol)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(MatchNestTheme.secondary)
            }

            ForEach(daysInVisibleMonth, id: \.self) { date in
                CalendarDayCell(
                    date: date,
                    isCurrentMonth: calendar.isDate(date, equalTo: monthDate, toGranularity: .month),
                    events: eventsFor(date)
                )
            }
        }
        .padding(12)
        .background(MatchNestTheme.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(MatchNestTheme.border, lineWidth: 1))
    }

    private var eventList: some View {
        VStack(alignment: .leading, spacing: 10) {
            if isLoading {
                ProgressView()
                    .tint(MatchNestTheme.orange)
                    .frame(maxWidth: .infinity, minHeight: 90)
            } else if groups.isEmpty {
                EmptyStateView(title: "Quiet month", subtitle: "No followed events in this month yet.")
            } else {
                ForEach(groups) { group in
                    VStack(alignment: .leading, spacing: 8) {
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

    private var weekdaySymbols: [String] {
        calendar.shortStandaloneWeekdaySymbols
    }

    private var daysInVisibleMonth: [Date] {
        guard let monthInterval = calendar.dateInterval(of: .month, for: monthDate),
              let firstWeek = calendar.dateInterval(of: .weekOfMonth, for: monthInterval.start),
              let lastWeek = calendar.dateInterval(of: .weekOfMonth, for: monthInterval.end.addingTimeInterval(-1))
        else {
            return []
        }

        var days: [Date] = []
        var current = firstWeek.start
        while current < lastWeek.end {
            days.append(current)
            current = calendar.date(byAdding: .day, value: 1, to: current) ?? current
        }
        return days
    }

    private func eventsFor(_ date: Date) -> [MatchEvent] {
        groups.flatMap(\.events).filter { event in
            guard let startsAt = event.startsAt else { return false }
            return calendar.isDate(startsAt, inSameDayAs: date)
        }
    }

    private func shiftMonth(_ offset: Int) {
        monthDate = calendar.date(byAdding: .month, value: offset, to: monthDate) ?? monthDate
        Task { await loadMonth() }
    }

    private func loadMonth() async {
        isLoading = true
        defer { isLoading = false }
        let components = calendar.dateComponents([.year, .month], from: monthDate)
        guard let year = components.year, let month = components.month else { return }
        do {
            groups = try await api.calendar(year: year, month: month, revealSpoilers: store.revealSpoilers)
        } catch {
            groups = []
        }
    }
}

struct CalendarDayCell: View {
    let date: Date
    let isCurrentMonth: Bool
    let events: [MatchEvent]

    var body: some View {
        VStack(spacing: 5) {
            Text(date.formatted(.dateTime.day()))
                .font(.caption.weight(.semibold))
                .foregroundStyle(isCurrentMonth ? MatchNestTheme.text : MatchNestTheme.secondary.opacity(0.45))
                .frame(maxWidth: .infinity, minHeight: 22)

            HStack(spacing: 3) {
                ForEach(Array(events.prefix(3))) { event in
                    Circle()
                        .fill(event.sport.color)
                        .frame(width: 5, height: 5)
                }
            }
            .frame(height: 6)
        }
        .padding(.vertical, 6)
        .background(events.isEmpty ? Color.clear : MatchNestTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 6))
    }
}

