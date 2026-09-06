from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from people.models import Technician


@login_required
def home(request):
    """Land supervisors/managers on the task list, everyone else on their own week."""
    technician = getattr(request.user, 'technician', None)
    if technician and technician.role in (Technician.Role.SUPERVISOR, Technician.Role.MANAGER):
        return redirect('tasks:task_list')
    return redirect('tasks:my_week')
