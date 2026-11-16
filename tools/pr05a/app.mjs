import { BrowserVault } from './cache.mjs';

const vault = new BrowserVault();
const status = document.querySelector('#status');
const content = document.querySelector('#content');
vault.addEventListener('locked', () => { content.textContent = ''; status.textContent = '已锁定'; });
const actions = {
  'login-a': () => vault.login('A'),
  'login-b': () => vault.login('B'),
  unlock: () => vault.unlock(),
  save: () => vault.save('PR05a 合成样本 α 中文 2026', 'sample'),
  read: async () => { content.textContent = await vault.load('sample'); },
  lock: () => vault.lock(),
  logout: () => vault.logout(),
  recover: () => vault.confirmRecovery(),
};
for (const [id, action] of Object.entries(actions)) {
  document.getElementById(id).addEventListener('click', async () => {
    const buttons = document.querySelectorAll('button');
    for (const button of buttons) button.disabled = true;
    try {
      await action();
      status.textContent = vault.status().unlocked ? '已解锁' : '已锁定';
    } catch (error) { status.textContent = `操作失败 ${error.message}`; }
    finally { for (const button of buttons) button.disabled = false; }
  });
}
// The isolated experiment runner imports BrowserVault itself. No key is exposed globally.
