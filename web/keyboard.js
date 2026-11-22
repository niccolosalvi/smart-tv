/* The device keyboard edits only these fields; a submit sends one complete request.
   No per-keystroke requests, query logging, browser storage or automatic retries. */
const youtubeSearchForm = document.querySelector('#youtube-search-form');
const youtubeQuery = document.querySelector('#youtube-query');
const youtubeUrlForm = document.querySelector('#youtube-url-form');
const youtubeUrl = document.querySelector('#youtube-url');
const isYoutubeLink = value => /^(https?:\/\/)?((www|m|music)\.)?(youtube\.com|youtu\.be)\//i.test(value.trim());

if (youtubeUrlForm && youtubeUrl) {
 youtubeUrlForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (pending) {
   status.textContent = 'Attendi il termine del comando in corso, poi premi Riproduci.';
   return;
  }
  const url = youtubeUrl.value.trim();
  if (!url || !isYoutubeLink(url)) {
   youtubeUrl.setCustomValidity('Inserisci un link YouTube valido (es. https://youtu.be/... o youtube.com/watch?v=...).');
   youtubeUrl.reportValidity();
   return;
  }
  const result = await command({youtube_url: url});
  if (result) youtubeUrl.value = '';
 });
 youtubeUrl.addEventListener('input', () => youtubeUrl.setCustomValidity(''));
}

youtubeSearchForm.addEventListener('submit', async event => {
 event.preventDefault();
 if (pending) {
  status.textContent = 'Attendi il termine del comando in corso, poi premi Cerca.';
  return;
 }
 const query = youtubeQuery.value;
 if (!query.trim()) {
  youtubeQuery.setCustomValidity('Scrivi cosa vuoi cercare.');
  youtubeQuery.reportValidity();
  return;
 }
 if (isYoutubeLink(query)) {
  const result = await command({youtube_url: query.trim()});
  if (result) youtubeQuery.value = '';
  return;
 }
 await command({youtube_search: query});
});
youtubeQuery.addEventListener('input', () => youtubeQuery.setCustomValidity(''));
