import Foundation
import HealthKit

enum HealthKitActivityType {
    static func displayName(rawValue: Int) -> String {
        guard rawValue >= 0,
              let activityType = HKWorkoutActivityType(rawValue: UInt(rawValue))
        else {
            return "Other workout"
        }

        switch activityType {
        case .running:
            return "Running"
        case .cycling:
            return "Cycling"
        case .swimming:
            return "Swimming"
        case .walking:
            return "Walking"
        case .hiking:
            return "Hiking"
        case .traditionalStrengthTraining:
            return "Strength training"
        case .functionalStrengthTraining:
            return "Functional strength training"
        case .yoga:
            return "Yoga"
        case .mixedCardio:
            return "Mixed cardio"
        default:
            return "Other workout"
        }
    }

    static func syncKey(rawValue: Int) -> String {
        guard rawValue >= 0,
              let activityType = HKWorkoutActivityType(rawValue: UInt(rawValue))
        else {
            return "other"
        }

        switch activityType {
        case .running:
            return "running"
        case .cycling:
            return "cycling"
        case .swimming:
            return "swimming"
        case .hiking:
            return "hiking"
        case .traditionalStrengthTraining:
            return "traditionalStrengthTraining"
        case .functionalStrengthTraining:
            return "functionalStrengthTraining"
        default:
            return "other"
        }
    }
}
