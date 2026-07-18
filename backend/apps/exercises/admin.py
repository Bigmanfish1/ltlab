from django.contrib import admin

from .models import Exercise, ExercisePart, Topic


class ExercisePartInline(admin.TabularInline):
    model = ExercisePart
    extra = 0


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("title", "exercise_type", "topic", "difficulty", "is_published")
    list_filter = ("exercise_type", "is_published", "difficulty")
    inlines = [ExercisePartInline]


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("title", "visible", "position")
