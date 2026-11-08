// Prints open (not completed) reminders from the Reminders app as JSON.
//   osascript -l JavaScript reminders.js '["List name", ...]'
// With no list names, reads every list.
function run(argv) {
  const want = argv[0] ? JSON.parse(argv[0]) : [];
  const R = Application('Reminders');
  const items = [];
  R.lists().forEach(list => {
    const listName = list.name();
    if (want.length && !want.includes(listName)) return;
    const rs = list.reminders.whose({ completed: false });
    const ids = rs.id(), names = rs.name(), dues = rs.dueDate();
    let flagged = [];
    try { flagged = rs.flagged(); } catch (e) {}
    for (let i = 0; i < ids.length; i++) {
      const due = dues[i];
      items.push({
        id: ids[i], name: names[i], list: listName, flagged: !!flagged[i],
        due: due ? due.toISOString() : null,
        // allDayDueDate can't be read in bulk; a due time of exactly local midnight means "all day".
        all_day: !!due && due.getHours() === 0 && due.getMinutes() === 0,
      });
    }
  });
  return JSON.stringify({ items });
}
