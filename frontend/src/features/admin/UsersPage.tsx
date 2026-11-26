import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type { Role, User } from "@/api/types";
import { useCurrentUser } from "@/features/auth/useAuth";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { formatDateTime } from "@/shared/lib/format";
import { options, ROLE_LABELS, ROLES } from "@/shared/lib/labels";
import {
  Badge,
  Button,
  Card,
  CheckboxField,
  DataTable,
  Dialog,
  EmptyState,
  FormMessage,
  PageHeader,
  Pagination,
  QueryView,
  SelectField,
  TextField,
} from "@/shared/ui";

import { useSaveUser, useUsers } from "./api";

const createSchema = z.object({
  full_name: z.string().trim().min(2, "Enter the full name").max(200, "Too long"),
  email: z.string().trim().min(1, "Email is required").email("Enter a valid email address"),
  role: z.enum(ROLES as [Role, ...Role[]], "Choose a role"),
  password: z.string().min(12, "Use at least 12 characters").max(128, "Use at most 128 characters"),
});

const editSchema = z.object({
  full_name: z.string().trim().min(2, "Enter the full name").max(200, "Too long"),
  role: z.enum(ROLES as [Role, ...Role[]], "Choose a role"),
  is_active: z.boolean(),
});

function CreateUserForm({ onDone }: { onDone: () => void }) {
  const save = useSaveUser();
  const form = useForm<z.infer<typeof createSchema>>({
    resolver: zodResolver(createSchema),
    defaultValues: { full_name: "", email: "", role: "LOAN_OFFICER", password: "" },
  });
  const { errors, isSubmitting } = form.formState;
  const submit = form.handleSubmit(async (values) => {
    try {
      await save.mutateAsync({ values });
      onDone();
    } catch (error) {
      applyServerErrors(error, form.setError, ["full_name", "email", "role", "password"]);
    }
  });
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <TextField
        label="Full name"
        required
        error={errors.full_name?.message}
        {...form.register("full_name")}
      />
      <TextField
        label="Email"
        type="email"
        required
        autoComplete="off"
        error={errors.email?.message}
        {...form.register("email")}
      />
      <SelectField
        label="Role"
        required
        options={options(ROLES, ROLE_LABELS)}
        error={errors.role?.message}
        {...form.register("role")}
      />
      <TextField
        label="Initial password"
        type="password"
        autoComplete="new-password"
        required
        hint="At least 12 characters. Share it with the person through a separate channel."
        error={errors.password?.message}
        {...form.register("password")}
      />
      <FormMessage error={save.error && !Object.keys(errors).length ? save.error : undefined} />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={isSubmitting}>
          Create user
        </Button>
      </div>
    </form>
  );
}

function EditUserForm({ user, onDone }: { user: User; onDone: () => void }) {
  const me = useCurrentUser();
  const save = useSaveUser();
  const form = useForm<z.infer<typeof editSchema>>({
    resolver: zodResolver(editSchema),
    defaultValues: { full_name: user.full_name, role: user.role, is_active: user.is_active },
  });
  const { errors, isSubmitting } = form.formState;
  const self = me.id === user.id;
  const submit = form.handleSubmit(async (values) => {
    try {
      await save.mutateAsync({ id: user.id, values });
      onDone();
    } catch (error) {
      applyServerErrors(error, form.setError, ["full_name", "role", "is_active"]);
    }
  });
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <p className="text-sm text-muted">{user.email}</p>
      <TextField
        label="Full name"
        required
        error={errors.full_name?.message}
        {...form.register("full_name")}
      />
      <SelectField
        label="Role"
        required
        disabled={self}
        hint={self ? "You can't change your own role." : undefined}
        options={options(ROLES, ROLE_LABELS)}
        error={errors.role?.message}
        {...form.register("role")}
      />
      <CheckboxField
        label="Active (can sign in)"
        disabled={self}
        hint={
          self
            ? "You can't deactivate yourself."
            : "Deactivating signs the person out of every session."
        }
        {...form.register("is_active")}
      />
      <FormMessage error={save.error && !Object.keys(errors).length ? save.error : undefined} />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={isSubmitting}>
          Save
        </Button>
      </div>
    </form>
  );
}

export function UsersPage() {
  usePageTitle("Users");
  const [role, setRole] = useState<Role | "">("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const users = useUsers(role || undefined, page);

  return (
    <>
      <PageHeader
        title="Users"
        description="Staff accounts and their roles. Applicants register themselves."
        actions={<Button onClick={() => setEditing("new")}>Add staff user</Button>}
      />
      <div className="mb-4 max-w-xs">
        <SelectField
          label="Role"
          value={role}
          onChange={(event) => {
            setRole(event.target.value as Role | "");
            setPage(1);
          }}
          placeholder="All roles"
          options={options(ROLES, ROLE_LABELS)}
        />
      </div>
      <Card>
        <QueryView
          query={users}
          isEmpty={(data) => data.items.length === 0}
          empty={<EmptyState title="No users match" />}
        >
          {(data) => (
            <>
              <DataTable<User>
                caption="Users"
                rows={data.items}
                rowKey={(row) => row.id}
                columns={[
                  {
                    key: "name",
                    header: "Name",
                    cell: (r) => <span className="font-medium">{r.full_name}</span>,
                  },
                  { key: "email", header: "Email", cell: (r) => r.email },
                  { key: "role", header: "Role", cell: (r) => ROLE_LABELS[r.role] },
                  {
                    key: "active",
                    header: "Status",
                    cell: (r) =>
                      r.is_active ? <Badge tone="positive">Active</Badge> : <Badge>Inactive</Badge>,
                  },
                  {
                    key: "login",
                    header: "Last sign-in",
                    cell: (r) => formatDateTime(r.last_login_at),
                  },
                  {
                    key: "edit",
                    header: <span className="sr-only">Actions</span>,
                    align: "right",
                    cell: (r) => (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing(r)}
                        aria-label={`Edit ${r.full_name}`}
                      >
                        Edit
                      </Button>
                    ),
                  },
                ]}
              />
              <Pagination
                page={data.page}
                pageSize={data.page_size}
                total={data.total}
                onPage={setPage}
              />
            </>
          )}
        </QueryView>
      </Card>
      <Dialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing === "new" ? "Add a staff user" : "Edit user"}
      >
        {editing === "new" && <CreateUserForm onDone={() => setEditing(null)} />}
        {editing && editing !== "new" && (
          <EditUserForm user={editing} onDone={() => setEditing(null)} />
        )}
      </Dialog>
    </>
  );
}
