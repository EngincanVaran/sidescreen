// Prints what the Spotify app is playing as JSON, optionally after a command.
//   osascript -l JavaScript spotify.js [playpause|next|previous]
// Never launches Spotify: if it isn't running, reports {running:false}.
function run(argv) {
  const S = Application('Spotify');
  if (!S.running()) return JSON.stringify({ running: false });
  const cmd = argv[0];
  if (cmd === 'playpause') S.playpause();
  else if (cmd === 'next') S.nextTrack();
  else if (cmd === 'previous') S.previousTrack();
  if (cmd) delay(0.3);  // let Spotify switch tracks before reading
  const state = String(S.playerState());
  if (state === 'stopped') return JSON.stringify({ running: true, state });
  const t = S.currentTrack();
  const get = f => { try { return f(); } catch (e) { return null; } };
  return JSON.stringify({
    running: true, state,
    id: get(() => t.id()),
    name: get(() => t.name()),
    artist: get(() => t.artist()),
    album: get(() => t.album()),
    artwork: get(() => t.artworkUrl()),
    duration: (get(() => t.duration()) || 0) / 1000,
    position: get(() => S.playerPosition()) || 0,
  });
}
