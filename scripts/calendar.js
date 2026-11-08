// Prints events from the Mac's calendars (everything Calendar.app shows) as JSON, via EventKit.
//   osascript -l JavaScript calendar.js <days_ahead> '["Calendar name", ...]'
// With no names, reads every calendar. Repeating events come back as separate occurrences.
ObjC.import('EventKit');
ObjC.import('Foundation');

function run(argv) {
  const days = Math.max(1, parseInt(argv[0] || '2', 10));
  const want = argv[1] ? JSON.parse(argv[1]) : [];
  const store = $.EKEventStore.alloc.init;

  // Ask for access (macOS shows its prompt once), and wait for the answer.
  let done = false, granted = false;
  const cb = (ok, err) => { granted = ok; done = true; };
  if (store.respondsToSelector('requestFullAccessToEventsWithCompletion:')) store.requestFullAccessToEventsWithCompletion(cb);
  else store.requestAccessToEntityTypeCompletion($.EKEntityTypeEvent, cb);
  for (let i = 0; i < 300 && !done; i++) $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
  if (!granted) return JSON.stringify({ error: 'no calendar access' });

  const start = $.NSCalendar.currentCalendar.startOfDayForDate($.NSDate.date);
  const end = start.dateByAddingTimeInterval(days * 86400);
  let cals = store.calendarsForEntityType($.EKEntityTypeEvent);
  if (want.length) {
    const keep = $.NSMutableArray.array;
    for (let i = 0; i < cals.count; i++) if (want.includes(cals.objectAtIndex(i).title.js)) keep.addObject(cals.objectAtIndex(i));
    cals = keep;
  }
  const events = store.eventsMatchingPredicate(store.predicateForEventsWithStartDateEndDateCalendars(start, end, cals));
  const iso = d => new Date(d.timeIntervalSince1970 * 1000).toISOString();
  const out = [];
  for (let i = 0; i < events.count; i++) {
    const e = events.objectAtIndex(i);
    if (e.status === 3) continue;  // cancelled
    out.push({
      title: e.title.js || '(no title)',
      location: e.location.isNil() ? '' : e.location.js,
      start: iso(e.startDate), end: iso(e.endDate),
      all_day: !!e.allDay, calendar: e.calendar.title.js,
    });
  }
  return JSON.stringify({ events: out });
}
