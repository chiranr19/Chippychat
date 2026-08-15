
/* chat.js – streamlined, passes sessionId to server */
const chatToggle  = document.getElementById('chat-toggle');
const chatBot     = document.getElementById('chatbot');
const minimizeBtn = document.getElementById('minimize-btn');
const chatBody    = document.getElementById('chat-body');
const chatInput   = document.getElementById('chat-input');
const sendBtn     = document.getElementById('send-btn');

const sessionId = crypto.randomUUID();   // ← unique per browser tab

// toggle visibility
chatToggle.onclick  = () => { chatBot.classList.remove('hidden'); chatToggle.style.display='none'; };
minimizeBtn.onclick = () => { chatBot.classList.add('hidden');   chatToggle.style.display='block'; };

// helper to add message bubbles
function addMessage(text, from='bot'){
  const div=document.createElement('div');
  div.className='message ' + (from==='bot'?'bot-message':'user-message');
  div.textContent=text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
}

// send to backend
function sendMessage(){
  const msg = chatInput.value.trim();
  if(!msg) return;
  addMessage(msg,'user');
  chatInput.value='';

  // loading bubble
  const loading = document.createElement('div');
  loading.className='message bot-message';
  loading.textContent='…';
  chatBody.appendChild(loading);
  chatBody.scrollTop = chatBody.scrollHeight;

  fetch('/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ message: msg, sessionId })
  })
  .then(r=>r.json())
  .then(({reply})=>{
     loading.remove();
     addMessage(reply,'bot');
  })
  .catch(()=>{
     loading.remove();
     addMessage('Server error – try again later.','bot');
  });
}

// bindings
sendBtn.onclick = sendMessage;
chatInput.addEventListener('keypress',e=>{
  if(e.key==='Enter'){ e.preventDefault(); sendMessage(); }
});

// greet
addMessage('Hi! Ask me anything – e.g. "Need a room for four guests next weekend in Chennai". I’ll understand 😊');
