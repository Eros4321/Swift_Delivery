from django import forms

from .background_removal import BackgroundRemovalError, remove_image_background
from .models import MenuItem


class MenuItemAdminForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = '__all__'

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image or getattr(image, '_committed', False):
            return image

        try:
            return remove_image_background(image)
        except BackgroundRemovalError as exc:
            raise forms.ValidationError(str(exc)) from exc
