"""Staff accounts: every staff login in one place, with what's wrong with
each and the actions to fix it — the staff side of customers' Customer
accounts screen. Every action is recorded in the Audit log.
"""
from datetime import timedelta

from axes.utils import reset as axes_reset
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from customers.accounts import locked_usernames, log_account_action
from customers.forms import ChangeLoginEmailForm
from people.models import Technician
from people.permissions import can_manage_account, is_last_active_admin, require_manager
from reference.models import Country

from .forms import TechnicianFirstLoginForm
from .views import (
    STAFF_LINK_VALID_DAYS, _generate_temporary_password, send_password_changed_email, send_staff_setup_link,
    sign_out_of_mobile_app, staff_user_for_link_token,
)


class StaffAccountStatus(models.TextChoices):
    NO_LOGIN = 'no_login', gettext_lazy('No login')
    INVITED = 'invited', gettext_lazy('Setup link sent')
    EXPIRED = 'expired', gettext_lazy('Setup link expired')
    TEMPORARY = 'temporary', gettext_lazy('Temporary password')
    ACTIVE = 'active', gettext_lazy('Active')
    LOCKED = 'locked', gettext_lazy('Locked')
    DISABLED = 'disabled', gettext_lazy('Login disabled')
    LEFT = 'left', gettext_lazy('Left the company')


def staff_account_status(technician, locked, now=None):
    user = technician.user
    if not technician.is_active:
        return StaffAccountStatus.LEFT
    if user is None:
        return StaffAccountStatus.NO_LOGIN
    if not user.is_active:
        return StaffAccountStatus.DISABLED
    if user.username.lower() in locked:
        return StaffAccountStatus.LOCKED
    if not user.has_usable_password():
        # Never set a password: waiting on their setup link.
        sent = technician.setup_link_sent_at
        if sent is None or (now or timezone.now()) - sent > timedelta(days=STAFF_LINK_VALID_DAYS):
            return StaffAccountStatus.EXPIRED
        return StaffAccountStatus.INVITED
    if technician.must_change_password:
        return StaffAccountStatus.TEMPORARY
    return StaffAccountStatus.ACTIVE


def unlock_login(username):
    """Clears a lockout for this login (axes locks by account, see
    settings.AXES_LOCKOUT_PARAMETERS)."""
    axes_reset(username=username)


def _redirect_back(request):
    return redirect(f"{reverse('tasks:staff_accounts')}?{request.POST.get('return_query', '')}")


def staff_setup(request, token):
    """Where a staff setup link lands: choose a password, then straight in.
    An expired or used link says so and points to the manager.
    """
    user = staff_user_for_link_token(token)
    if user is None:
        return render(request, 'tasks/staff_setup_invalid.html', {'days': STAFF_LINK_VALID_DAYS}, status=400)
    if request.user.is_authenticated and request.user != user:
        logout(request)
    person = user.technician
    form = TechnicianFirstLoginForm(request.POST or None, user=user)
    if request.method == 'POST' and form.is_valid():
        user.set_password(form.cleaned_data['new_password1'])
        user.save(update_fields=['password'])
        person.must_change_password = False
        person.save(update_fields=['must_change_password'])
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, _('Your password has been set. Welcome.'))
        return redirect('home')
    return render(request, 'tasks/staff_setup.html', {'form': form, 'person': person})


@login_required
def staff_accounts(request):
    """Every staff login, every country, for the manager tier: who has no
    login, who's on a temporary password, locked out, turned off or gone —
    and the fix for each, from this one screen.
    """
    actor = require_manager(request)
    locked = locked_usernames()

    if request.method == 'POST':
        target = get_object_or_404(Technician.objects.select_related('user'), pk=request.POST.get('technician'))
        if not can_manage_account(actor, target):
            raise PermissionDenied
        user = target.user
        action = request.POST.get('action')
        if user is None:
            return _redirect_back(request)

        if action == 'temporary_password' and user.is_active:
            # Shown once, right here, for reading out over the phone —
            # never emailed, never stored anywhere readable.
            password = _generate_temporary_password()
            user.set_password(password)
            user.save(update_fields=['password'])
            target.must_change_password = True
            target.save(update_fields=['must_change_password'])
            sign_out_of_mobile_app(user)
            send_password_changed_email(user, temporary=True)
            unlock_login(user.username)
            log_account_action(request, target, 'Staff temporary password set; signed out of the mobile app')
            return render(request, 'tasks/staff_account_password.html', {
                'person': target, 'password': password, 'return_query': request.POST.get('return_query', ''),
            })
        if action == 'send_setup_link' and user.is_active:
            if send_staff_setup_link(request, target):
                log_account_action(request, target, f'Staff setup link emailed to {user.email}')
                messages.success(request, _('A setup link has been sent to %(email)s.') % {'email': user.email})
            else:
                messages.error(request, _('No email address is recorded for this login. Please add one first.'))
        elif action == 'unlock':
            unlock_login(user.username)
            log_account_action(request, target, 'Staff login unlocked')
            messages.success(request, _('%(name)s has been unlocked.') % {'name': target.full_name})
        elif action == 'sign_out_phones':
            sign_out_of_mobile_app(user)
            log_account_action(request, target, 'Staff signed out of the mobile app on every phone')
            messages.success(request, _('%(name)s has been signed out of the mobile app on all devices.') % {
                'name': target.full_name,
            })
        elif action == 'disable' and user.is_active:
            if target.pk == actor.pk:
                messages.error(request, _('You cannot disable your own login.'))
            elif is_last_active_admin(target):
                messages.error(request, _('This is the only administrator able to sign in, so their login '
                                          'cannot be disabled.'))
            else:
                user.is_active = False
                user.save(update_fields=['is_active'])
                sign_out_of_mobile_app(user)
                log_account_action(request, target, 'Staff login turned off')
                messages.success(request, _('Login disabled for %(name)s.') % {'name': target.full_name})
        elif action == 'enable' and not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
            log_account_action(request, target, 'Staff login turned on')
            messages.success(request, _('Login enabled for %(name)s.') % {'name': target.full_name})
        elif action == 'change_email':
            form = ChangeLoginEmailForm(request.POST, user=user)
            if form.is_valid():
                old_email = user.email
                user.email = form.cleaned_data['email']
                fields = ['email']
                # Logins created in the app sign in with their email; keep
                # those two together. A login with its own sign-in name keeps it.
                if old_email and user.username.lower() == old_email.lower():
                    user.username = user.email
                    fields.append('username')
                user.save(update_fields=fields)
                log_account_action(request, target, f'Staff email changed from {old_email or "(none)"} to {user.email}')
                messages.success(request, _('The email address has been changed to %(email)s.') % {'email': user.email})
            else:
                messages.error(request, form.errors['email'][0])
        return _redirect_back(request)

    people = Technician.objects.select_related('user', 'country').annotate(
        app_sessions=Count('user__auth_token', distinct=True),
    ).order_by('full_name')
    country_id = request.GET.get('country', '')
    if country_id:
        people = people.filter(country_id=country_id)
    search = request.GET.get('q', '').strip()
    if search:
        people = people.filter(
            Q(full_name__icontains=search) | Q(user__username__icontains=search) | Q(user__email__icontains=search),
        )
    now = timezone.now()
    rows = [{'person': person, 'status': staff_account_status(person, locked, now)} for person in people]
    counts = {value: 0 for value in StaffAccountStatus.values}
    for row in rows:
        counts[row['status']] += 1
    status = request.GET.get('status', '')
    if status in StaffAccountStatus.values:
        rows = [row for row in rows if row['status'] == status]

    managed = next((row for row in rows if str(row['person'].pk) == request.GET.get('manage')), None)
    if managed is not None:
        managed['can_manage'] = can_manage_account(actor, managed['person'])
    query = request.GET.copy()
    query.pop('manage', None)
    return render(request, 'tasks/staff_accounts.html', {
        'managed': managed, 'list_query': query.urlencode(), 'return_query': request.GET.urlencode(),
        'page_obj': Paginator(rows, 50).get_page(request.GET.get('page')),
        'status_counts': [(value, label, counts[value]) for value, label in StaffAccountStatus.choices],
        'total': len(people), 'status': status, 'search': search, 'country_id': country_id,
        'countries': Country.objects.filter(is_active=True).order_by('name'),
    })
