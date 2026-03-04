document.addEventListener('DOMContentLoaded', function () {
    // Fonction pour ajouter le toggle mot de passe
    function addPasswordToggle() {
        const passwordFields = document.querySelectorAll('input[type="password"]');

        passwordFields.forEach(function (field) {
            // Éviter de dupliquer les toggles
            if (field.parentElement.querySelector('.password-toggle')) return;

            // Créer le conteneur
            const wrapper = document.createElement('div');
            wrapper.className = 'password-field';
            field.parentNode.insertBefore(wrapper, field);
            wrapper.appendChild(field);

            // Créer le bouton toggle
            const toggleBtn = document.createElement('i');
            toggleBtn.className = 'password-toggle fas fa-eye';
            wrapper.appendChild(toggleBtn);

            // Gérer le toggle
            toggleBtn.addEventListener('click', function () {
                const type = field.getAttribute('type') === 'password' ? 'text' : 'password';
                field.setAttribute('type', type);
                this.className = type === 'password' ? 'password-toggle fas fa-eye' : 'password-toggle fas fa-eye-slash';
            });
        });
    }

    // Appliquer au chargement
    addPasswordToggle();

    // Observer les changements de DOM (pour les modals etc.)
    const observer = new MutationObserver(function (mutations) {
        addPasswordToggle();
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true
    });

    // Amélioration des messages d'erreur
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function (alert) {
        // Ajouter une icône selon le type
        const icon = document.createElement('i');
        if (alert.classList.contains('alert-danger') || alert.classList.contains('alert-error')) {
            icon.className = 'fas fa-exclamation-circle mr-2';
        } else if (alert.classList.contains('alert-success')) {
            icon.className = 'fas fa-check-circle mr-2';
        } else if (alert.classList.contains('alert-info')) {
            icon.className = 'fas fa-info-circle mr-2';
        } else if (alert.classList.contains('alert-warning')) {
            icon.className = 'fas fa-exclamation-triangle mr-2';
        }
        alert.insertBefore(icon, alert.firstChild);
    });

    // Confirmation avant actions importantes
    const deleteButtons = document.querySelectorAll('.deletelink');
    deleteButtons.forEach(function (button) {
        button.addEventListener('click', function (e) {
            if (!confirm('Êtes-vous sûr de vouloir supprimer cet élément ?')) {
                e.preventDefault();
            }
        });
    });
});