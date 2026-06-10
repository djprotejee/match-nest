import SwiftUI

struct EventCard: View {
    let event: MatchEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                Rectangle()
                    .fill(event.sport.color)
                    .frame(width: 4)
                    .clipShape(Capsule())

                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(timeLabel)
                            .font(.system(.subheadline, design: .rounded).weight(.bold))
                            .foregroundStyle(MatchNestTheme.orange)
                        Spacer()
                        LevelBadge(level: event.followLevel)
                    }

                    Text(event.title)
                        .font(.headline)
                        .foregroundStyle(MatchNestTheme.text)
                        .lineLimit(2)

                    HStack(spacing: 8) {
                        SportBadge(sport: event.sport)
                        if let competition = event.competition {
                            Text(competition)
                                .font(.caption)
                                .foregroundStyle(MatchNestTheme.secondary)
                        }
                    }

                    if event.resultHidden {
                        Label("Score hidden", systemImage: "eye.slash")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(MatchNestTheme.violet)
                    } else if let result = event.resultSummary {
                        Text(result)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(MatchNestTheme.text)
                    }
                }
            }
        }
        .padding(14)
        .background(MatchNestTheme.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(MatchNestTheme.border, lineWidth: 1))
    }

    private var timeLabel: String {
        if event.status == .live {
            return "LIVE"
        }
        guard let startsAt = event.startsAt else {
            return "TBD"
        }
        return startsAt.formatted(date: .omitted, time: .shortened)
    }
}

struct SportBadge: View {
    let sport: Sport

    var body: some View {
        Text(sport.title)
            .font(.caption.weight(.bold))
            .foregroundStyle(sport.color)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(sport.color.opacity(0.14), in: Capsule())
    }
}

struct LevelBadge: View {
    let level: FollowLevel

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: level == .main ? "flame.fill" : "star.fill")
            Text(level.rawValue.capitalized)
        }
        .font(.caption2.weight(.bold))
        .foregroundStyle(level == .main ? MatchNestTheme.orange : MatchNestTheme.violet)
    }
}

