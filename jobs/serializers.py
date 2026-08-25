from rest_framework import serializers

from jobs.models import JobApplication
from jobs.models import JobOffer

# Plafond de poids d'un CV : un upload sans limite explicite laisse n'importe qui saturer
# le stockage (voir .claude/rules/security.md).
MAX_CV_BYTES = 5 * 1024 * 1024
# Signature réelle d'un fichier PDF (les 5 premiers octets). Le `FileExtensionValidator`
# posé sur le modèle ne regarde que l'extension du nom ; on vérifie ici le contenu réel
# pour qu'un `.pdf` renommé (exécutable, script...) soit rejeté.
_PDF_MAGIC = b"%PDF-"


class JobOfferSerializer(serializers.ModelSerializer):
    recruiter_name = serializers.ReadOnlyField(source="recruiter.first_name")
    company_verified = serializers.ReadOnlyField(source="company.is_verified")
    is_favorited = serializers.SerializerMethodField()

    class Meta:
        model = JobOffer
        fields = "__all__"
        read_only_fields = ["recruiter", "is_active"]

    def get_is_favorited(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return obj.favorited_by.filter(user=user).exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Si un profil entreprise (vérifié ou non) est lié, son nom prime sur le texte libre.
        if instance.company_id:
            data["company_name"] = instance.company.name
        return data


class JobApplicationSerializer(serializers.ModelSerializer):
    # `job` n'est qu'une PK : un recruteur/candidat ne peut pas afficher l'intitulé du poste
    # sans un aller-retour supplémentaire. Même pattern que les autres serializers de l'app.
    job_title = serializers.ReadOnlyField(source="job.title")
    # Absent jusqu'ici : un recruteur consultant ses candidatures reçues n'avait aucun moyen
    # de savoir QUI avait postulé (aucun champ candidat exposé du tout).
    applicant_name = serializers.SerializerMethodField()
    # `phone_number` est un objet `PhoneNumber` (pas une str) : un `ReadOnlyField` le
    # passerait tel quel au renderer JSON, qui ne sait pas l'encoder nativement (contrairement
    # à `PhoneNumberField` utilisé pour les champs directs de `User`, voir users/serializers.py).
    applicant_phone = serializers.SerializerMethodField()

    class Meta:
        model = JobApplication
        fields = [
            "id",
            "job",
            "job_title",
            "applicant_name",
            "applicant_phone",
            "cv_file",
            "message",
            "status",
            "applied_at",
        ]
        read_only_fields = ["status", "applied_at"]

    def get_applicant_name(self, obj):
        return f"{obj.applicant.first_name} {obj.applicant.last_name}".strip()

    def get_applicant_phone(self, obj):
        return str(obj.applicant.phone_number)

    def validate_cv_file(self, value):
        # `cv_file` reste optionnel (null=True/blank=True côté modèle) : un envoi vide ne
        # doit pas déclencher la validation de contenu.
        if value is None:
            return value

        size = getattr(value, "size", None)
        if size is not None and size > MAX_CV_BYTES:
            raise serializers.ValidationError(
                "CV trop lourd (%.1f Mo). Maximum autorisé : %d Mo."
                % (size / 1_000_000, MAX_CV_BYTES // (1024 * 1024))
            )

        # Type réel du contenu, pas seulement l'extension : on lit la signature en tête de
        # fichier puis on remet le curseur à zéro pour ne pas perturber l'upload/stockage.
        value.seek(0)
        header = value.read(len(_PDF_MAGIC))
        value.seek(0)
        if header != _PDF_MAGIC:
            raise serializers.ValidationError(
                "Fichier invalide : seul un PDF réel est accepté comme CV."
            )
        return value

    def validate_job(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Cette offre n'est plus active.")
        return value
