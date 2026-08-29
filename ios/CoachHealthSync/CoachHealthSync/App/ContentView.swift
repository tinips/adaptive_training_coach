import Foundation
import SwiftUI

struct ContentView: View {
    @ObservedObject var viewModel: SyncViewModel
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            Form {
                Section("Connection") {
                    if viewModel.isPaired {
                        Label("This iPhone is connected", systemImage: "checkmark.shield.fill")
                            .foregroundStyle(.green)
                        Text("To disconnect, use /disconnect_iphone in Telegram.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        Text("Send /connect_iphone to the Telegram bot, then enter the one-time code below.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        TextField("Pairing code", text: $viewModel.pairingCode)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .fontDesign(.monospaced)
                        Button("Connect iPhone") {
                            Task { await viewModel.pair() }
                        }
                        .disabled(viewModel.isWorking)
                    }
                }

                if viewModel.isPaired {
                    Section("Apple Health") {
                        Text("Coach Health Sync reads only workout summaries: activity type, dates, duration, distance, and active calories.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("Authorize Apple Health") {
                            Task { await viewModel.authorizeAndLoadWorkouts() }
                        }
                        .disabled(viewModel.isWorking)
                    }

                    Section {
                        HStack {
                            Text("Workouts from the last 3 months")
                            Spacer()
                            Button {
                                Task { await viewModel.refreshWorkouts() }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                            .disabled(viewModel.isWorking)
                            .accessibilityLabel("Refresh workouts")
                        }

                        if viewModel.workouts.isEmpty {
                            Text("Authorize Apple Health, then refresh to see workouts.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(viewModel.workouts) { workout in
                                WorkoutRow(
                                    workout: workout,
                                    isSelected: workout.id == viewModel.selectedWorkoutID
                                ) {
                                    viewModel.selectedWorkoutID = workout.id
                                }
                            }
                        }
                    }

                    Section {
                        Button("Sync now") {
                            Task { await viewModel.syncSelectedWorkout() }
                        }
                        .disabled(viewModel.selectedWorkoutID == nil || viewModel.isWorking)
                    }
                }

                if let statusMessage = viewModel.statusMessage {
                    Section {
                        Label(statusMessage, systemImage: "info.circle")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Coach Health Sync")
            .overlay {
                if viewModel.isWorking {
                    ProgressView()
                        .padding()
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            }
            .alert(
                "Couldn't complete that action",
                isPresented: Binding(
                    get: { viewModel.errorMessage != nil },
                    set: { if !$0 { viewModel.dismissError() } }
                )
            ) {
                Button("OK", role: .cancel) { viewModel.dismissError() }
            } message: {
                Text(viewModel.errorMessage ?? "")
            }
            .task {
                viewModel.prepare()
                await viewModel.autoSyncOnLaunch()
            }
            .onChange(of: scenePhase) { _, newPhase in
                guard newPhase == .active else { return }
                Task { await viewModel.autoSyncOnLaunch() }
            }
        }
    }
}

private struct WorkoutRow: View {
    let workout: HealthKitWorkout
    let isSelected: Bool
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Color.accentColor : .secondary)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(workout.activityDisplayName)
                        .foregroundStyle(.primary)
                    Text(workout.startDate.formatted(date: .abbreviated, time: .shortened))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Text(detailText)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Text("Source: \(workout.sourceName)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(workout.activityDisplayName), \(detailText), source \(workout.sourceName)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var detailText: String {
        var details = [
            Self.durationFormatter.string(from: workout.durationSeconds)
                ?? "\(Int(workout.durationSeconds.rounded())) sec"
        ]
        if let distanceMeters = workout.distanceMeters {
            details.append(String(format: "%.2f km", distanceMeters / 1_000))
        }
        if let caloriesKcal = workout.caloriesKcal {
            details.append("\(Int(caloriesKcal.rounded())) kcal")
        }
        return details.joined(separator: " | ")
    }

    private static let durationFormatter: DateComponentsFormatter = {
        let formatter = DateComponentsFormatter()
        formatter.allowedUnits = [.hour, .minute, .second]
        formatter.unitsStyle = .abbreviated
        formatter.zeroFormattingBehavior = .pad
        return formatter
    }()
}
