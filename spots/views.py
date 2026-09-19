from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from people.models import Technician


@login_required
def home(request):
    """Land supervisors/managers on the task list, a customer login on
    their own portal, and everyone else (a technician) on their own week.
    One shared login page for every account type — this is the only
    place that has to know how to route each of them afterward.
    """
    if hasattr(request.user, 'customer'):
        return redirect('customers:portal_home')
    technician = getattr(request.user, 'technician', None)
    if technician and technician.role in (Technician.Role.SUPERVISOR, Technician.Role.MANAGER):
        return redirect('tasks:task_list')
    return redirect('tasks:my_week')
