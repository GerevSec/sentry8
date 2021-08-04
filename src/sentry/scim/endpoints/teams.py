import logging
import re

import sentry_sdk
from django.db import IntegrityError, transaction
from django.template.defaultfilters import slugify
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from sentry.api.endpoints.organization_teams import OrganizationTeamsEndpoint
from sentry.api.endpoints.team_details import TeamDetailsEndpoint, TeamSerializer
from sentry.api.exceptions import ResourceDoesNotExist
from sentry.api.paginator import GenericOffsetPaginator
from sentry.api.serializers import serialize
from sentry.api.serializers.models.team import TeamSCIMSerializer
from sentry.models import (
    AuditLogEntryEvent,
    OrganizationMember,
    OrganizationMemberTeam,
    Team,
    TeamStatus,
)
from sentry.utils.cursors import SCIMCursor

from .constants import (
    SCIM_400_INTEGRITY_ERROR,
    SCIM_400_INVALID_FILTER,
    SCIM_400_TOO_MANY_PATCH_OPS_ERROR,
    SCIM_400_UNSUPPORTED_ATTRIBUTE,
    SCIM_404_GROUP_RES,
    SCIM_404_USER_RES,
    GroupPatchOps,
)
from .utils import OrganizationSCIMTeamPermission, SCIMEndpoint, parse_filter_conditions

delete_logger = logging.getLogger("sentry.deletions.api")


CONFLICTING_SLUG_ERROR = "A team with this slug already exists."


class OrganizationSCIMTeamIndex(SCIMEndpoint, OrganizationTeamsEndpoint):
    permission_classes = (OrganizationSCIMTeamPermission,)
    team_serializer = TeamSCIMSerializer

    def should_add_creator_to_team(self, request):
        return False

    def get(self, request, organization):
        try:
            filter_val = parse_filter_conditions(request.GET.get("filter"))
        except ValueError:
            raise ParseError(detail=SCIM_400_INVALID_FILTER)

        if "members" in request.GET.get("excludedAttributes", []):
            exclude_members = True
        else:
            exclude_members = False
        queryset = Team.objects.filter(
            organization=organization, status=TeamStatus.VISIBLE
        ).order_by("slug")

        if filter_val:
            queryset = queryset.filter(slug=slugify(filter_val))

        def data_fn(offset, limit):
            return list(queryset[offset : offset + limit])

        def on_results(results):
            results = serialize(
                results, None, TeamSCIMSerializer(), exclude_members=exclude_members
            )
            return self.list_api_format(request, queryset, results)

        return self.paginate(
            request=request,
            on_results=on_results,
            paginator=GenericOffsetPaginator(data_fn=data_fn),
            default_per_page=int(request.GET.get("count", 100)),
            queryset=queryset,
            cursor_cls=SCIMCursor,
        )

    def post(self, request, organization):
        # shim displayName from SCIM api to "slug" in order to work with
        # our regular team index POST
        request.data.update({"slug": slugify(request.data["displayName"])})
        return super().post(request, organization)
