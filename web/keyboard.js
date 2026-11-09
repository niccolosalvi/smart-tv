/* The device keyboard edits only this field; a submit sends one complete query.
   No per-keystroke requests, query logging, browser storage or automatic retries. */
const youtubeSearchForm = document.querySelector('#youtube-search-form');
const youtubeQuery = document.querySelector('#youtube-query');
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
 await command({youtube_search: query});
});
youtubeQuery.addEventListener('input', () => youtubeQuery.setCustomValidity(''));
