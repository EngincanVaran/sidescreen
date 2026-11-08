// Sidescreen Calendar: prints events from the Mac's calendars as JSON (EventKit).
// It's a tiny app (not a script) because macOS only asks for calendar access on behalf of apps
// that say why they need it (NSCalendarsFullAccessUsageDescription in Info.plist).
//   sidescreen-calendar <days_ahead> '["Calendar name", ...]'   (no names = every calendar)
import EventKit
import Foundation

func emit(_ obj: Any) {
    let data = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data)
}

let args = CommandLine.arguments
let days = max(1, Int(args.count > 1 ? args[1] : "2") ?? 2)
var want: [String] = []
if args.count > 2, let d = args[2].data(using: .utf8), let names = try? JSONSerialization.jsonObject(with: d) as? [String] {
    want = names
}

let store = EKEventStore()
let done = DispatchSemaphore(value: 0)
var granted = false
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; done.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; done.signal() }
}
_ = done.wait(timeout: .now() + 180)  // the first time, this waits for the person to answer the prompt
guard granted else {
    emit(["error": "no calendar access"])
    exit(0)
}

let start = Calendar.current.startOfDay(for: Date())
let end = start.addingTimeInterval(Double(days) * 86400)
var calendars = store.calendars(for: .event)
if !want.isEmpty { calendars = calendars.filter { want.contains($0.title) } }
let iso = ISO8601DateFormatter()
var events: [[String: Any]] = []
if !calendars.isEmpty {
    for e in store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: calendars)) {
        if e.status == .canceled { continue }
        events.append([
            "title": (e.title ?? "").isEmpty ? "(no title)" : e.title!,
            "location": e.location ?? "",
            "start": iso.string(from: e.startDate),
            "end": iso.string(from: e.endDate ?? e.startDate),
            "all_day": e.isAllDay,
            "calendar": e.calendar?.title ?? "",
        ])
    }
}
emit(["events": events])
