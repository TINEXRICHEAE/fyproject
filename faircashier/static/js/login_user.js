    document.addEventListener('DOMContentLoaded', function () {
      const form = document.getElementById('loginForm');
      const emailInput = document.getElementById('email');
      const passwordInput = document.getElementById('password');
      const emailError = document.getElementById('emailError');
      const passwordError = document.getElementById('passwordError');
      const messageBox = document.getElementById('messageBox');
      const loginBtn = document.getElementById('loginBtn');
      const btnText = loginBtn.querySelector('.btn-text');

      // Add input focus effects
      const inputs = [emailInput, passwordInput];
      inputs.forEach(input => {
        input.addEventListener('focus', function() {
          this.parentElement.style.transform = 'scale(1.02)';
        });

        input.addEventListener('blur', function() {
          this.parentElement.style.transform = 'scale(1)';
        });

        // Real-time validation feedback
        input.addEventListener('input', function() {
          if (this === emailInput && this.value) {
            if (validateEmail(this.value)) {
              this.style.borderColor = 'var(--success)';
              emailError.style.display = 'none';
            } else {
              this.style.borderColor = 'var(--error)';
            }
          }
          if (this === passwordInput && this.value.length >= 1) {
            this.style.borderColor = this.value.length >= 6 ? 'var(--success)' : 'var(--primary-cyan)';
            passwordError.style.display = 'none';
          }
        });
      });

      // Form submission handler with enhanced loading state
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        let isValid = true;

        // Reset previous states
        emailError.style.display = 'none';
        passwordError.style.display = 'none';
        messageBox.style.display = 'none';

        // Validate email
        if (!validateEmail(emailInput.value)) {
          showFieldError(emailError, 'Please enter a valid email address.');
          emailInput.style.borderColor = 'var(--error)';
          isValid = false;
        } else {
          emailInput.style.borderColor = 'var(--success)';
        }

        // Validate password
        if (!passwordInput.value) {
          showFieldError(passwordError, 'Password is required.');
          passwordInput.style.borderColor = 'var(--error)';
          isValid = false;
        } else {
          passwordInput.style.borderColor = 'var(--success)';
        }

        // Submit form if valid
        if (isValid) {
          // Show loading state
          loginBtn.classList.add('loading');
          loginBtn.disabled = true;
          btnText.textContent = 'Logging in...';

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
              // Reset loading state
              loginBtn.classList.remove('loading');
              loginBtn.disabled = false;
              btnText.textContent = 'Login';

              if (data.error) {
                showMessage(data.error, 'error');
                // Add shake animation to form
                form.style.animation = 'shake 0.5s ease-in-out';
                setTimeout(() => {
                  form.style.animation = '';
                }, 500);
              } else {
                showMessage(data.message, 'success');
                // Success animation
                loginBtn.style.background = 'var(--success)';
                btnText.textContent = 'Success! Redirecting...';
                
                // Redirect after success animation
                setTimeout(() => {
                  window.location.href = "/";
                }, 1500);
              }
            })
            .catch((error) => {
              // Reset loading state
              loginBtn.classList.remove('loading');
              loginBtn.disabled = false;
              btnText.textContent = 'Login';
              
              showMessage('An error occurred. Please try again.', 'error');
              console.error('Login error:', error);
            });
        } else {
          // Add shake animation for validation errors
          form.style.animation = 'shake 0.5s ease-in-out';
          setTimeout(() => {
            form.style.animation = '';
          }, 500);
        }
      });

      // Helper functions
      function validateEmail(email) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return emailRegex.test(email);
      }

      function showFieldError(errorElement, message) {
        errorElement.textContent = message;
        errorElement.style.display = 'block';
        errorElement.style.animation = 'shake 0.5s ease-in-out';
      }

      function showMessage(message, type) {
        messageBox.textContent = message;
        messageBox.className = `message-box ${type}`;
        messageBox.style.display = 'block';
        
        // Auto-hide success messages
        if (type === 'success') {
          setTimeout(() => {
            messageBox.style.animation = 'fadeOut 0.5s ease-out forwards';
          }, 3000);
        }
      }

      // Add keyboard navigation enhancements
      document.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
          const inputs = [emailInput, passwordInput];
          const currentIndex = inputs.indexOf(e.target);
          if (currentIndex < inputs.length - 1) {
            e.preventDefault();
            inputs[currentIndex + 1].focus();
          }
        }
      });

      // Add smooth scroll to error
      function scrollToError(element) {
        element.scrollIntoView({ 
          behavior: 'smooth', 
          block: 'center' 
        });
      }
    });
