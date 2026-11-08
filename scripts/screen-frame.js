// Prints "x,y,w,h,count,name" (top-left origin, points) for the dashboard display.
//   osascript -l JavaScript screen-frame.js [name-substring]
// With a name, picks the first display whose name contains it (case-insensitive).
// Otherwise picks the smallest non-primary display (the portable screen), or the
// primary one if there's only one.
ObjC.import('AppKit');

function run(argv) {
  const screens = $.NSScreen.screens;
  const list = [];
  for (let i = 0; i < screens.count; i++) {
    const s = screens.objectAtIndex(i);
    list.push({ i, f: s.frame, name: s.localizedName ? s.localizedName.js : 'Display ' + i });
  }
  const primary = list[0];
  const want = (argv[0] || '').trim().toLowerCase();
  let target = want ? list.find(s => s.name.toLowerCase().includes(want)) : null;
  if (!target) {
    const others = list.slice(1).sort((a, b) => a.f.size.width * a.f.size.height - b.f.size.width * b.f.size.height);
    target = others[0] || primary;
  }
  // AppKit uses a bottom-left origin anchored to the primary screen; Chrome wants top-left.
  const f = target.f;
  const x = Math.round(f.origin.x);
  const y = Math.round(primary.f.size.height - (f.origin.y + f.size.height));
  return [x, y, Math.round(f.size.width), Math.round(f.size.height), list.length, target.name].join(',');
}
