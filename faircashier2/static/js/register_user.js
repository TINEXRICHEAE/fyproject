document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('registrationForm');
  const emailInput = document.getElementById('email');
  const passwordInput = document.getElementById('password');
  const roleSelect = document.getElementById('role');
  const adminCheckbox = document.getElementById('register_as_admin');

  const emailError = document.getElementById('emailError');
  const passwordError = document.getElementById('passwordError');
  const roleError = document.getElementById('roleError');

  const messageBox = document.getElementById('messageBox');
  const submitButton = form.querySelector('.btn');


    // Auto-focus password if email is pre-filled
    if (prefillEmail && emailInput.value) {
      passwordInput.focus();
    }


  // Password validation rules
  const passwordRules = {
    length: /.{8,}/,
    uppercase: /[A-Z]/,
    lowercase: /[a-z]/,
    digit: /\d/,
    special: /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/,
  };

  // Add smooth focus animations
  const inputs = document.querySelectorAll('input, select');
  inputs.forEach(input => {
    input.addEventListener('focus', function() {
      this.style.transform = 'translateY(-2px)';
      const label = this.parentNode.querySelector('label');
      if (label) {
        label.style.color = '#45edf2';
        label.style.textShadow = '0 0 10px rgba(69, 237, 242, 0.3)';
      }
    });

    input.addEventListener('blur', function() {
      this.style.transform = 'translateY(0)';
      const label = this.parentNode.querySelector('label');
      if (label) {
        label.style.color = '#e8e8fc';
        label.style.textShadow = 'none';
      }
    });

    // Add typing animation effect (skip for select)
    if (this.tagName === 'INPUT') {
      input.addEventListener('input', function() {
        this.style.boxShadow = '0 0 0 2px rgba(69, 237, 242, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.1)';
        setTimeout(() => {
          if (this === document.activeElement) {
            this.style.boxShadow = '0 0 0 2px rgba(69, 237, 242, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1)';
          }
        }, 200);
      });
    }
  });

  // Admin checkbox toggle logic
  adminCheckbox.addEventListener('change', function () {
    if (this.checked) {
      roleSelect.disabled = true;
      roleSelect.value = '';
      roleSelect.style.opacity = '0.6';
    } else {
      roleSelect.disabled = false;
      roleSelect.style.opacity = '1';
    }
  });

  // Enhanced password validation with smooth animations
  passwordInput.addEventListener('input', function () {
    const password = passwordInput.value;
    let validCount = 0;
    
    Object.keys(passwordRules).forEach((rule, index) => {
      const isValid = passwordRules[rule].test(password);
      const ruleElement = document.getElementById(rule);
      
      if (isValid) {
        validCount++;
        setTimeout(() => {
          ruleElement.classList.add('valid');
        }, index * 100); // Staggered animation
      } else {
        ruleElement.classList.remove('valid');
      }
    });

    // Add progress indication
    updatePasswordStrength(validCount);
  });

  // Password strength indicator
  function updatePasswordStrength(validCount) {
    const container = passwordInput.parentNode;
    let strengthBar = container.querySelector('.strength-bar');
    
    if (!strengthBar) {
      strengthBar = document.createElement('div');
      strengthBar.className = 'strength-bar';
      strengthBar.innerHTML = '<div class="strength-fill"></div>';
      strengthBar.style.cssText = `
        width: 100%;
        height: 3px;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 2px;
        margin-top: 8px;
        overflow: hidden;
      `;
      const fill = strengthBar.querySelector('.strength-fill');
      fill.style.cssText = `
        height: 100%;
        background: linear-gradient(90deg, #ff6b6b, #ffa500, #45edf2);
        border-radius: 2px;
        transition: all 0.3s ease;
        width: 0%;
      `;
      container.appendChild(strengthBar);
    }

    const fill = strengthBar.querySelector('.strength-fill');
    const percentage = (validCount / 5) * 100;
    fill.style.width = percentage + '%';
    
    if (validCount <= 2) {
      fill.style.background = '#ff6b6b';
    } else if (validCount <= 4) {
      fill.style.background = 'linear-gradient(90deg, #ff6b6b, #ffa500)';
    } else {
      fill.style.background = 'linear-gradient(90deg, #ffa500, #45edf2)';
      fill.style.boxShadow = '0 0 10px rgba(69, 237, 242, 0.5)';
    }
  }

  // Enhanced form submission with loading animation
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    let isValid = true;

    // Clear previous errors with animation
    hideError(emailError);
    hideError(passwordError);
    hideError(roleError);

    // Validate email with smooth error display
    if (!validateEmail(emailInput.value)) {
      showError(emailError, 'Please enter a valid email address.');
      addShakeAnimation(emailInput);
      isValid = false;
    }

    // Validate password
    if (!validatePassword(passwordInput.value)) {
      showError(passwordError, 'Password must meet all requirements.');
      addShakeAnimation(passwordInput);
      isValid = false;
    }

    // Validate role (unless admin is checked)
    const isAdminChecked = adminCheckbox.checked;
    if (!isAdminChecked && !roleSelect.value) {
      showError(roleError, 'Please select a role.');
      addShakeAnimation(roleSelect);
      isValid = false;
    }

    // Submit form if valid
    if (isValid) {
      // Add loading state
      submitButton.classList.add('loading');
      submitButton.disabled = true;
      
      const formData = new FormData(form);
      fetch(form.action, {
        method: 'POST',
        body: formData,
        headers: {
          'X-CSRFToken': formData.get('csrfmiddlewaretoken'),
        },
      })
        .then((response) => response.json())
        .then((data) => {
          // Remove loading state
          submitButton.classList.remove('loading');
          submitButton.disabled = false;
          
          if (data.error) {
            showMessage(data.error, 'error');
            addShakeAnimation(form);
          } else {
            showMessage(data.message, 'success');
            addSuccessAnimation(form);
            
            // Reset form with animation
            setTimeout(() => {
              resetFormWithAnimation();
            }, 1000);
            
            // Redirect after successful registration
            setTimeout(() => {
              window.location.href = "/login/";
            }, 2000);
          }
        })
        .catch((error) => {
          submitButton.classList.remove('loading');
          submitButton.disabled = false;
          showMessage('An error occurred. Please try again.', 'error');
          addShakeAnimation(form);
        });
    }
  });

  // Enhanced error display function
  function showError(errorElement, message) {
    errorElement.textContent = message;
    errorElement.style.display = 'block';
    errorElement.style.animation = 'none';
    setTimeout(() => {
      errorElement.style.animation = 'errorShake 0.5s ease-in-out, fadeInUp 0.3s ease-out';
    }, 10);
  }

  // Hide error function
  function hideError(errorElement) {
    if (errorElement.style.display === 'block') {
      errorElement.style.animation = 'fadeOut 0.3s ease-out';
      setTimeout(() => {
        errorElement.style.display = 'none';
      }, 300);
    }
  }

  // Add shake animation to elements
  function addShakeAnimation(element) {
    element.style.animation = 'none';
    setTimeout(() => {
      element.style.animation = 'errorShake 0.5s ease-in-out';
    }, 10);
  }

  // Add success animation
  function addSuccessAnimation(element) {
    element.style.transform = 'scale(1.02)';
    element.style.transition = 'transform 0.3s ease';
    setTimeout(() => {
      element.style.transform = 'scale(1)';
    }, 300);
  }

  // Reset form with smooth animation
  function resetFormWithAnimation() {
    const formGroups = form.querySelectorAll('.form-group');
    formGroups.forEach((group, index) => {
      setTimeout(() => {
        group.style.animation = 'fadeOut 0.3s ease-out';
        setTimeout(() => {
          const input = group.querySelector('input:not([type="checkbox"])');
          const select = group.querySelector('select');
          const checkbox = group.querySelector('input[type="checkbox"]');
          
          if (input) input.value = '';
          if (select) select.value = '';
          if (checkbox) checkbox.checked = false;

          group.style.animation = 'fadeInUp 0.5s ease-out';
        }, 300);
      }, index * 100);
    });

    // Reset password strength bar
    const strengthBar = passwordInput.parentNode.querySelector('.strength-bar');
    if (strengthBar) {
      const fill = strengthBar.querySelector('.strength-fill');
      fill.style.width = '0%';
      fill.style.boxShadow = 'none';
    }

    // Reset password validation indicators
    Object.keys(passwordRules).forEach(rule => {
      const ruleElement = document.getElementById(rule);
      ruleElement.classList.remove('valid');
    });

    // Re-enable role if admin was checked
    if (adminCheckbox.checked) {
      adminCheckbox.checked = false;
      roleSelect.disabled = false;
      roleSelect.style.opacity = '1';
    }
  }

  // Helper functions
  function validateEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
  }

  function validatePassword(password) {
    return Object.values(passwordRules).every((rule) => rule.test(password));
  }

  function showMessage(message, type) {
    messageBox.textContent = message;
    messageBox.className = `message-box ${type}`;
    messageBox.style.display = 'block';
    
    // Add entrance animation
    messageBox.style.animation = 'messageSlideIn 0.5s ease-out';
    
    // Auto hide success messages after 5 seconds
    if (type === 'success') {
      setTimeout(() => {
        messageBox.style.animation = 'fadeOut 0.5s ease-out';
        setTimeout(() => {
          messageBox.style.display = 'none';
        }, 500);
      }, 5000);
    }
  }

  // Add additional CSS animations via JavaScript
  const additionalStyles = `
    @keyframes fadeOut {
      from { opacity: 1; transform: translateY(0); }
      to { opacity: 0; transform: translateY(-10px); }
    }
    
    @keyframes pulseGlow {
      0%, 100% { box-shadow: 0 0 5px rgba(69, 237, 242, 0.3); }
      50% { box-shadow: 0 0 20px rgba(69, 237, 242, 0.6); }
    }
  `;
  
  const styleSheet = document.createElement('style');
  styleSheet.textContent = additionalStyles;
  document.head.appendChild(styleSheet);

  // Add hover effects to form elements
  const formGroups = document.querySelectorAll('.form-group');
  formGroups.forEach(group => {
    group.addEventListener('mouseenter', function() {
      this.style.transform = 'translateX(5px)';
      this.style.transition = 'transform 0.3s ease';
    });
    
    group.addEventListener('mouseleave', function() {
      this.style.transform = 'translateX(0)';
    });
  });

  // Add particle effect on successful validation
  function createParticleEffect(element) {
    const rect = element.getBoundingClientRect();
    const particle = document.createElement('div');
    particle.style.cssText = `
      position: fixed;
      top: ${rect.top + rect.height/2}px;
      left: ${rect.left + rect.width}px;
      width: 4px;
      height: 4px;
      background: #45edf2;
      border-radius: 50%;
      pointer-events: none;
      z-index: 1000;
      box-shadow: 0 0 10px #45edf2;
      animation: particleFloat 1s ease-out forwards;
    `;
    
    document.body.appendChild(particle);
    
    setTimeout(() => {
      particle.remove();
    }, 1000);
  }

  // Add particle animation CSS
  const particleStyles = `
    @keyframes particleFloat {
      0% { 
        opacity: 1; 
        transform: translate(0, 0) scale(1);
      }
      100% { 
        opacity: 0; 
        transform: translate(20px, -20px) scale(0);
      }
    }
  `;
  
  const particleStyleSheet = document.createElement('style');
  particleStyleSheet.textContent = particleStyles;
  document.head.appendChild(particleStyleSheet);

  // Trigger particle effects on password rule validation
  passwordInput.addEventListener('input', function () {
    const password = passwordInput.value;
    
    Object.keys(passwordRules).forEach((rule) => {
      const isValid = passwordRules[rule].test(password);
      const ruleElement = document.getElementById(rule);
      
      if (isValid && !ruleElement.classList.contains('valid')) {
        createParticleEffect(ruleElement);
      }
    });
  });

  // Add smooth scroll to error elements
  function scrollToError(element) {
    element.scrollIntoView({
      behavior: 'smooth',
      block: 'center'
    });
  }

  // Enhanced keyboard navigation
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') {
      e.preventDefault();
      const inputs = Array.from(form.querySelectorAll('input:not([type="checkbox"]), select'));
      const currentIndex = inputs.indexOf(e.target);
      
      if (currentIndex < inputs.length - 1) {
        inputs[currentIndex + 1].focus();
      } else {
        submitButton.focus();
      }
    }
  });

  // Add ripple effect to button
  submitButton.addEventListener('click', function(e) {
    const ripple = document.createElement('span');
    const rect = this.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const x = e.clientX - rect.left - size / 2;
    const y = e.clientY - rect.top - size / 2;
    
    ripple.style.cssText = `
      position: absolute;
      width: ${size}px;
      height: ${size}px;
      left: ${x}px;
      top: ${y}px;
      background: rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      transform: scale(0);
      animation: rippleEffect 0.6s ease-out;
      pointer-events: none;
    `;
    
    this.appendChild(ripple);
    
    setTimeout(() => {
      ripple.remove();
    }, 600);
  });

  // Ripple effect animation
  const rippleStyles = `
    @keyframes rippleEffect {
      to {
        transform: scale(2);
        opacity: 0;
      }
    }
  `;
  
  const rippleStyleSheet = document.createElement('style');
  rippleStyleSheet.textContent = rippleStyles;
  document.head.appendChild(rippleStyleSheet);

  // Add focus trap for better accessibility
  const focusableElements = form.querySelectorAll(
    'input, button, textarea, select, a[href], [tabindex]:not([tabindex="-1"])'
  );
  const firstElement = focusableElements[0];
  const lastElement = focusableElements[focusableElements.length - 1];

  form.addEventListener('keydown', function(e) {
    if (e.key === 'Tab') {
      if (e.shiftKey) {
        if (document.activeElement === firstElement) {
          lastElement.focus();
          e.preventDefault();
        }
      } else {
        if (document.activeElement === lastElement) {
          firstElement.focus();
          e.preventDefault();
        }
      }
    }
  });

  // Add form validation feedback sounds (optional - can be enabled/disabled)
  function playFeedbackSound(type) {
    // Create audio context for subtle UI sounds
    if (typeof AudioContext !== 'undefined' || typeof webkitAudioContext !== 'undefined') {
      const audioContext = new (AudioContext || webkitAudioContext)();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      if (type === 'success') {
        oscillator.frequency.value = 800;
        gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.3);
      } else if (type === 'error') {
        oscillator.frequency.value = 300;
        gainNode.gain.setValueAtTime(0.05, audioContext.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.2);
      }
      
      oscillator.start();
      oscillator.stop(audioContext.currentTime + 0.3);
    }
  }

  // Initialize form with entrance animation
  setTimeout(() => {
    form.style.animation = 'none';
    const elements = form.querySelectorAll('.form-group, .btn, .login-link');
    elements.forEach((el, index) => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(20px)';
      setTimeout(() => {
        el.style.transition = 'all 0.6s ease';
        el.style.opacity = '1';
        el.style.transform = 'translateY(0)';
      }, index * 100);
    });
  }, 500);

  // Add performance optimization for animations
  let animationId;
  function optimizeAnimations() {
    if (animationId) {
      cancelAnimationFrame(animationId);
    }
    
    animationId = requestAnimationFrame(() => {
      // Batch DOM updates here if needed
    });
  }

  // Cleanup function for when the page is unloaded
  window.addEventListener('beforeunload', () => {
    if (animationId) {
      cancelAnimationFrame(animationId);
    }
  });
});