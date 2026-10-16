// Déclaration MINIMALE d'une partie de l'API publique de Unity (UnityEngine), écrite par APEX d'après la
// documentation de Unity, pour vérifier que les scripts générés (`apex.brain.gamecode`) COMPILENT : syntaxe C#,
// noms et types des membres utilisés. Ce n'est PAS Unity : aucun comportement n'est simulé ni vérifié ici.
using System;
using System.Collections;
using System.Collections.Generic;

namespace UnityEngine
{
    public class Object { public string name; }
    public class Component : Object
    {
        public Transform transform;
        public GameObject gameObject;
        public T GetComponent<T>() { return default(T); }
        public T[] GetComponentsInChildren<T>() { return new T[0]; }
        public void SendMessageUpwards(string methodName, object value, SendMessageOptions options) { }
    }
    public class GameObject : Object { }
    public class Behaviour : Component { public bool enabled; }
    public class MonoBehaviour : Behaviour { }
    public enum SendMessageOptions { RequireReceiver, DontRequireReceiver }
    public enum Space { World, Self }
    public enum ForceMode { Force, Acceleration, Impulse, VelocityChange }
    public enum RigidbodyInterpolation { None, Interpolate, Extrapolate }
    public enum KeyCode { None, R, LeftShift, Space }

    public struct Vector3
    {
        public float x, y, z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public static Vector3 zero { get { return new Vector3(0, 0, 0); } }
        public static Vector3 up { get { return new Vector3(0, 1, 0); } }
        public static Vector3 right { get { return new Vector3(1, 0, 0); } }
        public static Vector3 forward { get { return new Vector3(0, 0, 1); } }
        public float sqrMagnitude { get { return x * x + y * y + z * z; } }
        public Vector3 normalized { get { return this; } }
        public static Vector3 operator +(Vector3 a, Vector3 b) { return a; }
        public static Vector3 operator -(Vector3 a, Vector3 b) { return a; }
        public static Vector3 operator *(Vector3 a, float d) { return a; }
        public static Vector3 operator *(float d, Vector3 a) { return a; }
        public static Vector3 operator /(Vector3 a, float d) { return a; }
        public static Vector3 ProjectOnPlane(Vector3 vector, Vector3 planeNormal) { return vector; }
        public static float SignedAngle(Vector3 from, Vector3 to, Vector3 axis) { return 0f; }
        public static Vector3 ClampMagnitude(Vector3 vector, float maxLength) { return vector; }
        public static Vector3 Cross(Vector3 lhs, Vector3 rhs) { return lhs; }
    }
    public struct Quaternion
    {
        public static Quaternion Euler(float x, float y, float z) { return new Quaternion(); }
        public static Quaternion LookRotation(Vector3 forward) { return new Quaternion(); }
        public static Quaternion Slerp(Quaternion a, Quaternion b, float t) { return a; }
        public static Quaternion operator *(Quaternion lhs, Quaternion rhs) { return lhs; }
    }
    public struct Bounds
    {
        public Bounds(Vector3 center, Vector3 size) { this.center = center; }
        public Vector3 center;
        public void Encapsulate(Bounds bounds) { }
    }
    public struct Color { public static Color red { get { return new Color(); } } }

    public class Transform : Component, IEnumerable
    {
        public Vector3 position;
        public Quaternion rotation;
        public Vector3 forward;
        public Vector3 right;
        public Vector3 up;
        public Transform root;
        public void RotateAround(Vector3 point, Vector3 axis, float angle) { }
        public bool IsChildOf(Transform parent) { return false; }
        public IEnumerator GetEnumerator() { return new List<Transform>().GetEnumerator(); }
    }
    public class Renderer : Component { public Bounds bounds; }
    public class Collider : Component { }
    public class Rigidbody : Component
    {
        public float mass;
        public RigidbodyInterpolation interpolation;
        public Quaternion rotation;
        public Vector3 position;
        public void MoveRotation(Quaternion rot) { }
        public void MovePosition(Vector3 position) { }
        public void AddForceAtPosition(Vector3 force, Vector3 position, ForceMode mode) { }
    }
    public class CharacterController : Collider
    {
        public float height;
        public float radius;
        public Vector3 center;
        public bool isGrounded;
        public void Move(Vector3 motion) { }
    }
    public class Camera : Behaviour { public static Camera main; }
    public struct RaycastHit
    {
        public Collider collider;
        public Rigidbody rigidbody;
        public Vector3 point;
    }
    public static class Physics
    {
        public static bool Raycast(Vector3 origin, Vector3 direction, out RaycastHit hitInfo, float maxDistance)
        {
            hitInfo = new RaycastHit();
            return false;
        }
        public static Collider[] OverlapSphere(Vector3 position, float radius) { return new Collider[0]; }
    }
    public static class Mathf
    {
        public const float Rad2Deg = 57.29578f;
        public static float MoveTowards(float current, float target, float maxDelta) { return target; }
        public static float Clamp(float value, float min, float max) { return value; }
        public static float Abs(float f) { return f; }
        public static float Max(float a, float b) { return a; }
        public static float Sqrt(float f) { return f; }
    }
    public static class Input
    {
        public static float GetAxis(string axisName) { return 0f; }
        public static bool GetButton(string buttonName) { return false; }
        public static bool GetButtonDown(string buttonName) { return false; }
        public static bool GetKey(KeyCode key) { return false; }
        public static bool GetKeyDown(KeyCode key) { return false; }
    }
    public static class Time
    {
        public static float time;
        public static float deltaTime;
        public static float fixedDeltaTime;
    }
    public static class Debug
    {
        public static void DrawLine(Vector3 start, Vector3 end, Color color, float duration) { }
    }
    [AttributeUsage(AttributeTargets.Field)]
    public class HeaderAttribute : Attribute { public HeaderAttribute(string header) { } }
    [AttributeUsage(AttributeTargets.Field)]
    public class TooltipAttribute : Attribute { public TooltipAttribute(string tooltip) { } }
    [AttributeUsage(AttributeTargets.Class, AllowMultiple = true)]
    public class RequireComponent : Attribute { public RequireComponent(Type requiredComponent) { } }
}
