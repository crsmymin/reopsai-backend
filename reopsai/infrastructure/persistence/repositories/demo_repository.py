from sqlalchemy import func, select

from reopsai.infrastructure.persistence.models.core import Company, CompanyMember, User


class DemoRepository:
    INDIVIDUAL_DEMO_EMAIL = "test@example.com"
    ENTERPRISE_DEMO_EMAIL = "demo-enterprise@test.com"

    @staticmethod
    def get_user_by_email_lower(session, email):
        return session.execute(
            select(User).where(func.lower(User.email) == email.lower()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_demo_user(session, *, email, tier, account_type):
        user = User(
            email=email,
            google_id=f"dev_{email}",
            tier=tier,
            account_type=account_type,
        )
        session.add(user)
        session.flush()
        session.refresh(user)
        return user

    @staticmethod
    def get_company_by_name_lower(session, name):
        return session.execute(
            select(Company).where(func.lower(Company.name) == name.lower()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_company(session, *, name, status):
        company = Company(name=name, status=status)
        session.add(company)
        session.flush()
        session.refresh(company)
        return company

    @staticmethod
    def get_company_member(session, *, company_id, user_id):
        return session.execute(
            select(CompanyMember)
            .where(CompanyMember.company_id == company_id, CompanyMember.user_id == user_id)
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_company_member(session, *, company_id, user_id, role):
        membership = CompanyMember(company_id=company_id, user_id=user_id, role=role)
        session.add(membership)
        return membership

