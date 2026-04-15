const PACKAGES = {
  starter:  { name: 'Starter Pack',  credits: '20 credits', price: '₱49',  amount: 49 },
  standard: { name: 'Standard Pack', credits: '50 credits', price: '₱99',  amount: 99 },
  pro:      { name: 'Pro Pack',      credits: '120 credits', price: '₱199', amount: 199 },
};

let selectedPkg = 'starter';
let selectedMethod = 'gcash';

function openModal(pkg) {
  selectedPkg = pkg;
  const p = PACKAGES[pkg];
  document.getElementById('modalTitle').textContent = `Buy ${p.name}`;
  document.getElementById('modalPkgName').textContent = p.name;
  document.getElementById('modalPkgCredits').textContent = p.credits;
  document.getElementById('modalPkgPrice').textContent = p.price;
  document.getElementById('modalOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('open');
  document.body.style.overflow = '';
  const btn = document.querySelector('.btn-checkout');
  btn.disabled = false;
  btn.textContent = 'Proceed to Payment →';
}

function closeModalOutside(e) {
  if (e.target === document.getElementById('modalOverlay')) closeModal();
}

function selectMethod(el, method) {
  document.querySelectorAll('.pay-method').forEach(m => m.classList.remove('selected'));
  el.classList.add('selected');
  selectedMethod = method;
}

function handleCheckout() {
  const btn = document.querySelector('.btn-checkout');
  btn.disabled = true;
  btn.textContent = 'Redirecting…';

  fetch('/payment/create/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: JSON.stringify({ package: selectedPkg, method: selectedMethod }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.checkout_url) {
      window.location.href = data.checkout_url;
    } else {
      alert('Payment error: ' + (data.error || 'Unknown error.'));
      btn.disabled = false;
      btn.textContent = 'Proceed to Payment →';
    }
  })
  .catch(() => {
    alert('Network error. Please try again.');
    btn.disabled = false;
    btn.textContent = 'Proceed to Payment →';
  });
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[2]) : '';
}

function toggleFaq(el) {
  const answer = el.nextElementSibling;
  const isOpen = answer.classList.contains('open');
  document.querySelectorAll('.faq-a').forEach(a => a.classList.remove('open'));
  document.querySelectorAll('.faq-q').forEach(q => q.classList.remove('open'));
  if (!isOpen) { answer.classList.add('open'); el.classList.add('open'); }
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

window.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('payment') === 'failed') {
    alert('Payment was cancelled or failed. No charges were made.');
    window.history.replaceState({}, '', '/pricing/');
  }
  if (params.get('payment') === 'success') {
    alert('Payment received! Your credits will appear shortly.');
    window.history.replaceState({}, '', '/pricing/');
  }
});


function toggleNav() {
  const nav = document.getElementById('mobileNav');
  const btn = document.getElementById('hamburger');
  nav.classList.toggle('open');
  btn.classList.toggle('open');
}

// Close nav when a link is tapped
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', () => {
    document.getElementById('mobileNav').classList.remove('open');
    document.getElementById('hamburger').classList.remove('open');
  });
});