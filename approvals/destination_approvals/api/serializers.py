from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected a JSON object.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({"unknown_fields": sorted(unknown)})
        return super().to_internal_value(data)


class SubmitSerializer(StrictSerializer):
    expected_review_id = serializers.IntegerField(min_value=1, allow_null=True)
    snapshot_hash = serializers.RegexField(r"^[0-9a-f]{64}$", trim_whitespace=False)


class DecisionSerializer(StrictSerializer):
    review_id = serializers.IntegerField(min_value=1)
    team = serializers.CharField(max_length=255)
    action = serializers.ChoiceField(choices=("approve", "deny"))
    comment = serializers.CharField(max_length=2000, allow_blank=True, required=False, default="")
